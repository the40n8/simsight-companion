package server

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/the40n8/simsight-companion/companion/bridge/internal/update"
)

func TestSetupChecksForUpdates(t *testing.T) {
	gh := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"tag_name":"v2026.12.01","assets":[]}`))
	}))
	defer gh.Close()
	s, dir := newTestSetup(t)
	s.links = func() (string, string) { return "", "starting" } // the game is running
	s.Updates = update.New("v2026.11.25", dir)
	s.Updates.API = gh.URL

	get := func() string {
		req := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:47800/setup?update=1", nil)
		req.RemoteAddr = "127.0.0.1:50000"
		w := httptest.NewRecorder()
		s.ServeHTTP(w, req)
		return w.Body.String()
	}
	if page := get(); !strings.Contains(page, "Installed: SimSight v2026.11.25") || !strings.Contains(page, "Check for updates") {
		t.Fatal("the page should show the version and the check button")
	}
	if w := setupPost(t, s, url.Values{"csrf": {s.csrf}, "action": {"update-check"}}, ""); w.Code != http.StatusSeeOther {
		t.Fatalf("check: %d", w.Code)
	}
	page := get()
	for _, want := range []string{`role="status"`, "SimSight v2026.12.01 is available.", "Download SimSight v2026.12.01", "installed when you quit the game"} {
		if !strings.Contains(page, want) {
			t.Errorf("page lacks %q", want)
		}
	}
	// Without a matching zip, downloading says so instead of failing silently.
	setupPost(t, s, url.Values{"csrf": {s.csrf}, "action": {"update-get"}}, "")
	if page := get(); !strings.Contains(page, `role="alert"`) || !strings.Contains(page, "was not downloaded") {
		t.Error("a failed download should be reported")
	}
	// Other sites cannot start a download.
	if w := setupPost(t, s, url.Values{"csrf": {s.csrf}, "action": {"update-get"}}, "https://evil.example"); w.Code != http.StatusForbidden {
		t.Errorf("cross-site update: %d", w.Code)
	}
}
