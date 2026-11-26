// Package pairing keeps the phone link stable across game sessions: a
// random token and port stored on the Mac, and the link written to a file
// the player can copy (mac/phone-link.sh).
package pairing

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
)

const DefaultPort = 47800

type Config struct {
	Token string `json:"token"`
	Port  int    `json:"port"`
	// Optional fixed address: a Cloudflare named tunnel (token from the
	// Cloudflare dashboard) and its public hostname, e.g.
	// "https://sims.example.com". Empty: a random quick tunnel each session.
	TunnelToken string `json:"tunnel_token,omitempty"`
	PublicURL   string `json:"public_url,omitempty"`
	// Optional meeting points: addresses of SimSight Workers
	// (companion/meeting-point), your own or shared ones. The phone app then
	// lives there, at one fixed address, and finds this Mac by itself each
	// session. This Mac announces itself to each of them.
	MeetingPoints []string `json:"meeting_points,omitempty"`
	// This Mac at the meeting points (made when meeting_points is set).
	MacID     string `json:"mac_id,omitempty"`
	MacSecret string `json:"mac_secret,omitempty"`
	// No Cloudflare tunnel: the phone app on the home Wi-Fi only.
	LocalOnly bool `json:"local_only,omitempty"`
	// Which versions the setup page offers: "stable" (empty: everyone's
	// releases) or "testing" (also builds published for trying first).
	UpdateChannel string `json:"update_channel,omitempty"`
}

// Load reads dir/config.json, creating it with a fresh token if missing.
func Load(dir string) (*Config, error) {
	path := filepath.Join(dir, "config.json")
	var cfg Config
	b, err := os.ReadFile(path)
	switch {
	case err == nil:
		if err := json.Unmarshal(b, &cfg); err != nil {
			return nil, fmt.Errorf("read %s: %w", path, err)
		}
	case errors.Is(err, fs.ErrNotExist):
	default:
		return nil, err
	}
	changed := false
	if len(cfg.Token) < 20 {
		cfg.Token = NewToken()
		changed = true
	}
	if cfg.Port == 0 {
		cfg.Port = DefaultPort
		changed = true
	}
	if len(cfg.MeetingPoints) > 0 && (len(cfg.MacID) < 20 || len(cfg.MacSecret) < 20) {
		cfg.MacID = NewToken()
		cfg.MacSecret = NewToken()
		changed = true
	}
	if changed {
		if err := cfg.save(path); err != nil {
			return nil, err
		}
	}
	return &cfg, nil
}

// NewToken returns 24 random bytes, URL-safe base64.
func NewToken() string {
	b := make([]byte, 24)
	if _, err := rand.Read(b); err != nil {
		panic(err) // crypto/rand never fails on macOS
	}
	return base64.RawURLEncoding.EncodeToString(b)
}

// Save writes dir/config.json (private to this user).
func (c *Config) Save(dir string) error {
	return c.save(filepath.Join(dir, "config.json"))
}

func (c *Config) save(path string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	b, _ := json.MarshalIndent(c, "", "  ")
	return os.WriteFile(path, append(b, '\n'), 0o600)
}

// Links returns phone links: the home network's IPv4 address first (what
// phones open most reliably), then the Mac's Bonjour name (survives an IP
// change), then other private addresses (VPNs, virtual networks). The token
// is in the fragment, so it never appears in request lines or logs.
func (c *Config) Links() []string {
	ips := lanIPs()
	var hosts []string
	if len(ips) > 0 {
		hosts = append(hosts, ips[0])
	}
	if out, err := exec.Command("/usr/sbin/scutil", "--get", "LocalHostName").Output(); err == nil {
		if name := strings.TrimSpace(string(out)); name != "" {
			hosts = append(hosts, name+".local")
		}
	}
	if len(ips) > 1 {
		hosts = append(hosts, ips[1:]...)
	}
	links := make([]string, 0, len(hosts))
	for _, h := range hosts {
		links = append(links, fmt.Sprintf("http://%s:%d/#k=%s", h, c.Port, c.Token))
	}
	return links
}

// defaultInterface is the interface of the default route (the home Wi-Fi or
// Ethernet), so its address is listed before VPNs and virtual networks.
func defaultInterface() string {
	out, err := exec.Command("/sbin/route", "-n", "get", "default").Output()
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(out), "\n") {
		if name, ok := strings.CutPrefix(strings.TrimSpace(line), "interface:"); ok {
			return strings.TrimSpace(name)
		}
	}
	return ""
}

func lanIPs() []string {
	var ips []string
	ifaces, _ := net.Interfaces()
	primary := defaultInterface()
	sort.SliceStable(ifaces, func(i, j int) bool {
		return ifaces[i].Name == primary && ifaces[j].Name != primary
	})
	for _, iface := range ifaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addrs, _ := iface.Addrs()
		for _, a := range addrs {
			if n, ok := a.(*net.IPNet); ok && n.IP.To4() != nil && n.IP.IsPrivate() {
				ips = append(ips, n.IP.String())
			}
		}
	}
	return ips
}

// WriteLinkFile writes the links to dir/phone-link.txt (first line = best).
func (c *Config) WriteLinkFile(dir string) (string, error) {
	path := filepath.Join(dir, "phone-link.txt")
	return path, os.WriteFile(path, []byte(strings.Join(c.Links(), "\n")+"\n"), 0o600)
}
