package update

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestNewer(t *testing.T) {
	for _, c := range []struct {
		have, tag string
		want      bool
	}{
		{"v2026.11.25", "v2026.11.26", true},
		{"v2026.11.25", "v2026.11.25-2", true},
		{"v2026.11.25-2", "v2026.11.25-10", true},
		{"v2026.11.25-2", "v2026.11.25", false},
		{"v2026.12.01", "v2026.11.30", false},
		{"v2026.11.25", "v2026.11.25", false},
		{"dev", "v2030.01.01", false},
		{"v2026.11.25", "nonsense", false},
	} {
		if got := Newer(c.have, c.tag); got != c.want {
			t.Errorf("Newer(%q, %q) = %v", c.have, c.tag, got)
		}
	}
}

// fakeGitHub serves a release whose zip holds an installer that records
// what it was given.
func fakeGitHub(t *testing.T, files map[string]string, tamper bool) *httptest.Server {
	t.Helper()
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for name, body := range files {
		h := &zip.FileHeader{Name: name, Method: zip.Deflate}
		h.SetMode(0o755)
		w, err := zw.CreateHeader(h)
		if err != nil {
			t.Fatal(err)
		}
		_, _ = w.Write([]byte(body))
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	zipBytes := buf.Bytes()
	sum := sha256.Sum256(zipBytes)
	if tamper {
		sum[0] ^= 1
	}
	mux := http.NewServeMux()
	var srv *httptest.Server
	mux.HandleFunc("/repos/"+Repo+"/releases/latest", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"tag_name": "v2026.12.01", "html_url": "https://example.invalid/r",
			"assets": []map[string]string{
				{"name": Asset(), "browser_download_url": srv.URL + "/dl/zip"},
				{"name": "SHA256SUMS", "browser_download_url": srv.URL + "/dl/sums"},
			},
		})
	})
	mux.HandleFunc("/dl/zip", func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write(zipBytes) })
	mux.HandleFunc("/dl/sums", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(hex.EncodeToString(sum[:]) + "  " + Asset() + "\n"))
	})
	srv = httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

func testUpdater(t *testing.T, srv *httptest.Server) *Updater {
	u := New("v2026.11.25", t.TempDir())
	u.API = srv.URL
	return u
}

func TestDownloadAndInstall(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the Mac installer runs under zsh")
	}
	out := filepath.Join(t.TempDir(), "ran")
	script := "#!/bin/zsh\nprint -r -- \"wait=$SIMSIGHT_WAIT_GAME speak=$SIMSIGHT_SPEAK\" > " + out + "\necho installed\n"
	srv := fakeGitHub(t, map[string]string{
		Package + "/install.sh":                  script,
		Package + "/SimSight/SimSight.ts4script": "mod",
		"__MACOSX/" + Package + "/._install.sh":  "junk",
	}, false)
	u := testUpdater(t, srv)
	ctx := context.Background()
	rel, err := u.Check(ctx)
	if err != nil || rel.Tag != "v2026.12.01" {
		t.Fatalf("check: %v %+v", err, rel)
	}
	if err := u.Download(ctx, rel); err != nil {
		t.Fatal(err)
	}
	if u.Ready() != "v2026.12.01" {
		t.Fatalf("ready = %q", u.Ready())
	}
	if err := u.Install(ctx, true); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(out)
	if strings.TrimSpace(string(got)) != "wait=180 speak=0" {
		t.Fatalf("installer saw %q", got)
	}
	if u.Ready() != "" {
		t.Fatal("still ready after install")
	}
	u.Version = "v2026.12.01"
	if u.JustInstalled() != "v2026.12.01" || u.JustInstalled() != "" {
		t.Fatal("installed news should be told once")
	}
}

func TestRefusesTamperedDownload(t *testing.T) {
	srv := fakeGitHub(t, map[string]string{Package + "/install.sh": "x", Package + "/install.ps1": "x"}, true)
	u := testUpdater(t, srv)
	rel, err := u.Check(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if err := u.Download(context.Background(), rel); err == nil || !strings.Contains(err.Error(), "fingerprint") {
		t.Fatalf("want a fingerprint error, got %v", err)
	}
	if u.Ready() != "" {
		t.Fatal("a tampered download must not be ready")
	}
}

func TestRefusesPathsOutsideTheFolder(t *testing.T) {
	srv := fakeGitHub(t, map[string]string{"../evil.sh": "x"}, false)
	u := testUpdater(t, srv)
	rel, _ := u.Check(context.Background())
	if err := u.Download(context.Background(), rel); err == nil || !strings.Contains(err.Error(), "unsafe") {
		t.Fatalf("want an unsafe path error, got %v", err)
	}
}

func TestSourceBuildIsNotUpdated(t *testing.T) {
	u := New("dev", t.TempDir())
	if u.Released() || u.Ready() != "" {
		t.Fatal("a build from source is not a release")
	}
	if err := u.Install(context.Background(), false); err == nil {
		t.Fatal("nothing to install")
	}
}

func TestChannels(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/repos/"+Repo+"/releases/latest", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"tag_name": "v2026.12.01"})
	})
	mux.HandleFunc("/repos/"+Repo+"/releases", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]map[string]any{
			{"tag_name": "v2026.12.09", "draft": true},
			{"tag_name": "v2026.12.03", "prerelease": true},
			{"tag_name": "v2026.12.01"},
			{"tag_name": "not-a-release", "prerelease": true},
		})
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	u := New("v2026.11.25", t.TempDir())
	u.API = srv.URL
	ctx := context.Background()

	rel, err := u.Check(ctx)
	if err != nil || rel.Tag != "v2026.12.01" || rel.Testing || u.ChannelName() != Stable {
		t.Fatalf("stable: %v %+v", err, rel)
	}
	u.SetChannel(Testing)
	rel, err = u.Check(ctx)
	if err != nil || rel.Tag != "v2026.12.03" || !rel.Testing {
		t.Fatalf("testing: %v %+v", err, rel)
	}
	u.SetChannel("nonsense")
	if u.ChannelName() != Stable {
		t.Fatal("an unknown channel must be stable")
	}
	if rel, _ = u.Check(ctx); rel.Tag != "v2026.12.01" {
		t.Fatalf("back on stable: %+v", rel)
	}
}
