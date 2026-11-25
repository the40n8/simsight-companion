// Package update finds a newer SimSight Companion on GitHub, downloads it
// with its SHA-256 fingerprint checked, and installs it by running the
// release's own installer (the same one as the one-line install, so the
// backups and checks are the same). The game loads the mod only when it
// starts, so with the game running an update is downloaded and installed
// when the game quits.
package update

import (
	"archive/zip"
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/the40n8/simsight-companion/companion/bridge/internal/osutil"
)

const (
	Repo = "the40n8/simsight-companion"
	// Package is the folder inside a release zip.
	Package  = "SimSight-Companion"
	maxBytes = 400 << 20
)

// ErrNoRelease: nothing is published on GitHub yet.
var ErrNoRelease = errors.New("no SimSight release is published yet")

// Release is a published version.
type Release struct {
	Tag    string            // "v2026.11.25"
	Page   string            // its GitHub page
	Assets map[string]string // file name -> download address
}

// Asset is this computer's release zip.
func Asset() string {
	if runtime.GOOS == "windows" {
		return "SimSight-Companion-windows.zip"
	}
	return "SimSight-Companion-mac.zip"
}

// Updater checks, downloads and installs updates. Its files live in
// <data>/update.
type Updater struct {
	Version string // this build: a release tag, or "dev" built from source
	Dir     string // data folder
	API     string // https://api.github.com (tests: a local server)
	Client  *http.Client
	// UserDir is The Sims 4 user folder (Windows installer: -UserDir).
	UserDir string

	mu      sync.Mutex
	latest  *Release
	checked time.Time
}

// New checks GitHub (SIMSIGHT_UPDATE_API: another server, for testing).
func New(version, dataDir string) *Updater {
	api := "https://api.github.com"
	if v := os.Getenv("SIMSIGHT_UPDATE_API"); v != "" {
		api = strings.TrimRight(v, "/")
	}
	return &Updater{Version: version, Dir: dataDir, API: api,
		Client: &http.Client{Timeout: 10 * time.Minute}}
}

func (u *Updater) folder() string { return filepath.Join(u.Dir, "update") }

var tagRE = regexp.MustCompile(`^v(\d{4})\.(\d{2})\.(\d{2})(?:-(\d+))?$`)

func parseTag(tag string) (int, bool) {
	m := tagRE.FindStringSubmatch(tag)
	if m == nil {
		return 0, false
	}
	y, _ := strconv.Atoi(m[1])
	mo, _ := strconv.Atoi(m[2])
	d, _ := strconv.Atoi(m[3])
	n := 1
	if m[4] != "" {
		n, _ = strconv.Atoi(m[4])
	}
	return ((y*100+mo)*100+d)*1000 + n, true
}

// Newer reports whether tag is a later release than have. A build from
// source ("dev") is never offered a release over it.
func Newer(have, tag string) bool {
	h, ok1 := parseTag(have)
	t, ok2 := parseTag(tag)
	return ok1 && ok2 && t > h
}

// Released reports whether this build is a release (it can be updated).
func (u *Updater) Released() bool {
	_, ok := parseTag(u.Version)
	return ok
}

// Check asks GitHub for the latest release (at most once a minute).
func (u *Updater) Check(ctx context.Context) (*Release, error) {
	u.mu.Lock()
	if u.latest != nil && time.Since(u.checked) < time.Minute {
		defer u.mu.Unlock()
		return u.latest, nil
	}
	u.mu.Unlock()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.API+"/repos/"+Repo+"/releases/latest", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("User-Agent", "SimSight-Companion")
	res, err := u.Client.Do(req)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode == http.StatusNotFound {
		return nil, ErrNoRelease
	}
	if res.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("GitHub answered %s", res.Status)
	}
	var body struct {
		Tag    string `json:"tag_name"`
		Page   string `json:"html_url"`
		Assets []struct {
			Name string `json:"name"`
			URL  string `json:"browser_download_url"`
		} `json:"assets"`
	}
	if err := json.NewDecoder(io.LimitReader(res.Body, 1<<20)).Decode(&body); err != nil {
		return nil, err
	}
	rel := &Release{Tag: body.Tag, Page: body.Page, Assets: map[string]string{}}
	for _, a := range body.Assets {
		rel.Assets[a.Name] = a.URL
	}
	u.mu.Lock()
	u.latest, u.checked = rel, time.Now()
	u.mu.Unlock()
	return rel, nil
}

type staged struct {
	Tag       string `json:"tag"`
	Installer string `json:"installer"`
}

// Ready is the tag of a downloaded update waiting to be installed, if any.
func (u *Updater) Ready() string {
	s, err := u.readStaged()
	if err != nil || !Newer(u.Version, s.Tag) {
		return ""
	}
	if _, err := os.Stat(s.Installer); err != nil {
		return ""
	}
	return s.Tag
}

func (u *Updater) readStaged() (staged, error) {
	var s staged
	b, err := os.ReadFile(filepath.Join(u.folder(), "ready.json"))
	if err == nil {
		err = json.Unmarshal(b, &s)
	}
	return s, err
}

// Download fetches rel for this computer, checks its fingerprint against
// the release's SHA256SUMS and unpacks it, ready to install.
func (u *Updater) Download(ctx context.Context, rel *Release) error {
	zipURL, sumsURL := rel.Assets[Asset()], rel.Assets["SHA256SUMS"]
	if zipURL == "" || sumsURL == "" {
		return errors.New("this release has no " + Asset())
	}
	if err := os.RemoveAll(u.folder()); err != nil {
		return err
	}
	work := filepath.Join(u.folder(), rel.Tag)
	if err := os.MkdirAll(work, 0o700); err != nil {
		return err
	}
	sums := filepath.Join(work, "SHA256SUMS")
	if err := u.fetch(ctx, sumsURL, sums, 1<<20); err != nil {
		return err
	}
	want, err := sumFor(sums, Asset())
	if err != nil {
		return err
	}
	zipPath := filepath.Join(work, Asset())
	if err := u.fetch(ctx, zipURL, zipPath, maxBytes); err != nil {
		return err
	}
	if got, err := fileSum(zipPath); err != nil || got != want {
		_ = os.RemoveAll(u.folder())
		return errors.New("the download does not match its fingerprint, so it was not kept")
	}
	if err := unzip(zipPath, filepath.Join(work, "x")); err != nil {
		return err
	}
	installer := filepath.Join(work, "x", Package, "install.sh")
	if runtime.GOOS == "windows" {
		installer = filepath.Join(work, "x", Package, "install.ps1")
	}
	if _, err := os.Stat(installer); err != nil {
		return errors.New("the download has no installer")
	}
	b, _ := json.Marshal(staged{Tag: rel.Tag, Installer: installer})
	return os.WriteFile(filepath.Join(u.folder(), "ready.json"), b, 0o600)
}

func (u *Updater) fetch(ctx context.Context, url, path string, limit int64) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "SimSight-Companion")
	res, err := u.Client.Do(req)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		return fmt.Errorf("download failed: %s", res.Status)
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	n, err := io.Copy(f, io.LimitReader(res.Body, limit+1))
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err == nil && n > limit {
		err = errors.New("the download is too large")
	}
	return err
}

func sumFor(sums, name string) (string, error) {
	f, err := os.Open(sums)
	if err != nil {
		return "", err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		fields := strings.Fields(sc.Text())
		if len(fields) == 2 && strings.TrimPrefix(fields[1], "*") == name && len(fields[0]) == 64 {
			return strings.ToLower(fields[0]), nil
		}
	}
	return "", errors.New("the release has no fingerprint for " + name)
}

func fileSum(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// unzip unpacks plain files and folders only, all inside dest.
func unzip(zipPath, dest string) error {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer r.Close()
	for _, f := range r.File {
		name := filepath.FromSlash(f.Name)
		if strings.HasPrefix(f.Name, "__MACOSX/") || strings.HasPrefix(filepath.Base(name), "._") {
			continue
		}
		if filepath.IsAbs(name) || !filepath.IsLocal(name) {
			return fmt.Errorf("unsafe path in the download: %q", f.Name)
		}
		target := filepath.Join(dest, name)
		mode := f.Mode()
		switch {
		case mode.IsDir():
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		case !mode.IsRegular():
			return fmt.Errorf("unexpected file type in the download: %q", f.Name)
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		if err := extract(f, target, mode.Perm()|0o600); err != nil {
			return err
		}
	}
	return nil
}

func extract(f *zip.File, target string, perm os.FileMode) error {
	rc, err := f.Open()
	if err != nil {
		return err
	}
	defer rc.Close()
	out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, perm)
	if err != nil {
		return err
	}
	_, err = io.Copy(out, io.LimitReader(rc, maxBytes))
	if cerr := out.Close(); err == nil {
		err = cerr
	}
	return err
}

// Install runs the downloaded update's installer and waits for it. With
// afterGame, the installer first waits for the game to finish quitting.
// The installer's output goes to <data>/update/install.log.
func (u *Updater) Install(ctx context.Context, afterGame bool) error {
	s, err := u.readStaged()
	if err != nil || !Newer(u.Version, s.Tag) {
		return errors.New("no update is ready")
	}
	cmd, err := u.installer(ctx, s.Installer, afterGame)
	if err != nil {
		return err
	}
	logPath := filepath.Join(u.folder(), "install.log")
	logFile, err := os.Create(logPath)
	if err != nil {
		return err
	}
	defer logFile.Close()
	cmd.Stdout, cmd.Stderr = logFile, logFile
	cmd.Dir = filepath.Dir(s.Installer)
	undo := moveSelfAside()
	if err := cmd.Run(); err != nil {
		undo()
		return fmt.Errorf("the installer stopped (%v): %s", err, lastLine(logPath))
	}
	// The update stays in the backups the installer made; the download goes.
	_ = os.RemoveAll(filepath.Join(u.folder(), s.Tag))
	_ = os.Remove(filepath.Join(u.folder(), "ready.json"))
	_ = os.WriteFile(filepath.Join(u.folder(), "installed.txt"), []byte(s.Tag+"\n"), 0o600)
	return nil
}

func (u *Updater) installer(ctx context.Context, path string, afterGame bool) (*exec.Cmd, error) {
	wait := "0"
	if afterGame {
		wait = "180"
	}
	if runtime.GOOS == "windows" {
		if _, err := os.Stat(filepath.Join(u.UserDir, "Options.ini")); err != nil {
			return nil, errors.New("The Sims 4 settings were not found next to SimSight")
		}
		args := []string{"-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path, "-Yes", "-UserDir", u.UserDir, "-WaitForGame", wait}
		cmd := exec.CommandContext(ctx, "powershell.exe", args...)
		osutil.HideWindow(cmd)
		return cmd, nil
	}
	cmd := exec.CommandContext(ctx, "/bin/zsh", path)
	cmd.Env = append(os.Environ(), "SIMSIGHT_SPEAK=0", "SIMSIGHT_WAIT_GAME="+wait)
	return cmd, nil
}

// moveSelfAside lets the installer replace this running program: Windows
// refuses to overwrite a running .exe but allows renaming it. The old copy
// is deleted at the next start (Cleanup). The returned func puts it back
// if the installer failed and left no new one.
func moveSelfAside() func() {
	if runtime.GOOS != "windows" {
		return func() {}
	}
	exe, err := os.Executable()
	if err != nil {
		return func() {}
	}
	old := exe + ".old"
	_ = os.Remove(old)
	if os.Rename(exe, old) != nil {
		return func() {}
	}
	return func() {
		if _, err := os.Stat(exe); err != nil {
			_ = os.Rename(old, exe)
		}
	}
}

// Cleanup removes the copy of the program an update moved aside.
func Cleanup() {
	if exe, err := os.Executable(); err == nil {
		_ = os.Remove(exe + ".old")
	}
}

// JustInstalled reports, once, the tag of an update installed since the last
// start, when it is this build.
func (u *Updater) JustInstalled() string {
	path := filepath.Join(u.folder(), "installed.txt")
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	_ = os.Remove(path)
	if tag := strings.TrimSpace(string(b)); tag == u.Version {
		return tag
	}
	return ""
}

// LastLog is the end of the last installer run, for the page.
func (u *Updater) LastLog() string { return lastLine(filepath.Join(u.folder(), "install.log")) }

func lastLine(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	lines := strings.Split(strings.TrimSpace(strings.ReplaceAll(string(b), "\r", "")), "\n")
	return strings.TrimSpace(lines[len(lines)-1])
}
