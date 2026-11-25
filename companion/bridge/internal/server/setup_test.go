package server

import (
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"testing"

	"github.com/the40n8/simsight-companion/companion/bridge/internal/pairing"
)

var fakeTunnelToken = base64.StdEncoding.EncodeToString([]byte(`{"a":"acct","t":"tunnel-id","s":"secret"}`))

func setupPost(t *testing.T, s *Setup, form url.Values, origin string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:47800/setup", strings.NewReader(form.Encode()))
	req.RemoteAddr = "127.0.0.1:50000"
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	if origin != "" {
		req.Header.Set("Origin", origin)
	}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, req)
	return w
}

func newTestSetup(t *testing.T) (*Setup, string) {
	dir := t.TempDir()
	if _, err := pairing.Load(dir); err != nil {
		t.Fatal(err)
	}
	return NewSetup(dir, 47800, nil), dir
}

func TestSetupSavesFixedAddress(t *testing.T) {
	s, dir := newTestSetup(t)
	w := setupPost(t, s, url.Values{
		"csrf": {s.csrf}, "action": {"save"}, "mode": {"fixed"},
		"tunnel_token": {"sudo cloudflared service install " + fakeTunnelToken},
		"public_url":   {"Sims.Example.com/"},
	}, "http://127.0.0.1:47800")
	if w.Code != http.StatusSeeOther {
		t.Fatalf("status %d: %s", w.Code, w.Body)
	}
	cfg, _ := pairing.Load(dir)
	if cfg.TunnelToken != fakeTunnelToken || cfg.PublicURL != "https://sims.example.com" || cfg.LocalOnly {
		t.Fatalf("saved %+v", cfg)
	}
	// Empty token field keeps the saved one.
	w = setupPost(t, s, url.Values{"csrf": {s.csrf}, "mode": {"fixed"}, "public_url": {"sims.example.com"}}, "")
	if cfg, _ = pairing.Load(dir); w.Code != http.StatusSeeOther || cfg.TunnelToken != fakeTunnelToken {
		t.Fatalf("token not kept: %d %+v", w.Code, cfg)
	}
	// The page never shows the token.
	get := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:47800/setup?saved=1", nil)
	get.RemoteAddr = "127.0.0.1:50000"
	rec := httptest.NewRecorder()
	s.ServeHTTP(rec, get)
	if strings.Contains(rec.Body.String(), fakeTunnelToken) || !strings.Contains(rec.Body.String(), `role="status"`) {
		t.Fatal("page leaks the token or lacks the saved message")
	}
}

func TestSetupRejectsBadInput(t *testing.T) {
	s, dir := newTestSetup(t)
	before, _ := pairing.Load(dir)
	w := setupPost(t, s, url.Values{"csrf": {s.csrf}, "mode": {"fixed"}, "tunnel_token": {"hello"}, "public_url": {"http://sims.example.com"}}, "")
	body := w.Body.String()
	if w.Code != http.StatusUnprocessableEntity || !strings.Contains(body, "2 problems") ||
		!strings.Contains(body, `href="#tunnel_token"`) || !strings.Contains(body, `aria-invalid="true"`) {
		t.Fatalf("status %d: %s", w.Code, body)
	}
	if after, _ := pairing.Load(dir); !reflect.DeepEqual(after, before) {
		t.Fatal("config changed despite errors")
	}
}

func TestSetupLocalOnlyAndMeetingPoint(t *testing.T) {
	s, dir := newTestSetup(t)
	if w := setupPost(t, s, url.Values{"csrf": {s.csrf}, "mode": {"local"}, "meeting_points": {"meet.example.com"}}, ""); w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("meeting point with Wi-Fi only accepted: %d", w.Code)
	}
	// One per line; repeats dropped; bad lines refused.
	if w := setupPost(t, s, url.Values{"csrf": {s.csrf}, "mode": {"quick"}, "meeting_points": {"meet.example.com/x\nnot an address"}}, ""); w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("bad meeting point accepted: %d", w.Code)
	}
	if w := setupPost(t, s, url.Values{"csrf": {s.csrf}, "mode": {"quick"}, "meeting_points": {"meet.example.com\r\nhttps://other.example.org\nmeet.example.com"}}, ""); w.Code != http.StatusSeeOther {
		t.Fatalf("status %d", w.Code)
	}
	cfg, _ := pairing.Load(dir)
	if !reflect.DeepEqual(cfg.MeetingPoints, []string{"https://meet.example.com", "https://other.example.org"}) || cfg.MacID == "" || cfg.MacSecret == "" {
		t.Fatalf("saved %+v", cfg)
	}
	setupPost(t, s, url.Values{"csrf": {s.csrf}, "mode": {"local"}}, "")
	if cfg, _ = pairing.Load(dir); !cfg.LocalOnly || len(cfg.MeetingPoints) != 0 {
		t.Fatalf("saved %+v", cfg)
	}
}

func TestSetupRefusesOtherSites(t *testing.T) {
	s, dir := newTestSetup(t)
	before, _ := pairing.Load(dir)
	if w := setupPost(t, s, url.Values{"csrf": {s.csrf}, "action": {"reset"}, "confirm": {"yes"}}, "https://evil.example"); w.Code != http.StatusForbidden {
		t.Fatalf("other origin: %d", w.Code)
	}
	if w := setupPost(t, s, url.Values{"csrf": {s.csrf}, "action": {"reset"}, "confirm": {"yes"}}, "null"); w.Code != http.StatusForbidden {
		t.Fatalf("null origin: %d", w.Code)
	}
	if w := setupPost(t, s, url.Values{"csrf": {"wrong"}, "action": {"reset"}, "confirm": {"yes"}}, ""); w.Code != http.StatusForbidden {
		t.Fatalf("wrong csrf: %d", w.Code)
	}
	req := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:47800/setup", nil)
	req.RemoteAddr = "127.0.0.1:50000"
	req.Header.Set("Cf-Ray", "x") // through the tunnel
	w := httptest.NewRecorder()
	s.ServeHTTP(w, req)
	if w.Code != http.StatusNotFound {
		t.Fatalf("tunnel request: %d", w.Code)
	}
	if after, _ := pairing.Load(dir); after.Token != before.Token {
		t.Fatal("token changed")
	}
}

func TestSetupReset(t *testing.T) {
	s, dir := newTestSetup(t)
	before, _ := pairing.Load(dir)
	if w := setupPost(t, s, url.Values{"csrf": {s.csrf}, "action": {"reset"}}, ""); w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("reset without confirm: %d", w.Code)
	}
	if w := setupPost(t, s, url.Values{"csrf": {s.csrf}, "action": {"reset"}, "confirm": {"yes"}}, ""); w.Code != http.StatusSeeOther {
		t.Fatalf("reset: %d", w.Code)
	}
	if after, _ := pairing.Load(dir); after.Token == before.Token || len(after.Token) < 20 {
		t.Fatal("token not replaced")
	}
}

func TestWebAddress(t *testing.T) {
	for in, want := range map[string]string{
		"sims.example.com":          "https://sims.example.com",
		"https://SIMS.example.com/": "https://sims.example.com",
		"http://localhost:8787":     "http://localhost:8787",
		"http://sims.example.com":   "",
		"sims":                      "",
		"https://x.com/path":        "",
		"ftp://x.com":               "",
	} {
		got, err := WebAddress(in)
		if (want == "") != (err != nil) || got != want {
			t.Errorf("%q: got %q, %v", in, got, err)
		}
	}
}
