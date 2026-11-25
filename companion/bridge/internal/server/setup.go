package server

import (
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"html/template"
	"log/slog"
	"net/http"
	"net/url"
	"slices"
	"strings"
	"sync"

	"github.com/the40n8/simsight-companion/companion/bridge/internal/osutil"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/pairing"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/update"
)

// Setup is the settings page, on this Mac only (http://127.0.0.1:<port>/setup):
// how the phone reaches the Mac, the meeting point, disconnecting phones, and
// updates.
// It writes config.json; the bridge reads it when the game starts.
type Setup struct {
	dir  string
	port int
	// links is the running bridge's phone link; nil when the page is served
	// by "simsight-bridge setup" with the game closed.
	links LinkFunc
	csrf  string
	// OnDone runs when the player presses "Done" (standalone page: exit).
	OnDone func()
	// Updates checks and installs new versions; nil hides that part.
	Updates *update.Updater

	mu         sync.Mutex
	latest     string // latest release found by "Check for updates"
	updateNote string // what the last update action did, shown once
	updateBad  bool
}

func NewSetup(dir string, port int, links LinkFunc) *Setup {
	return &Setup{dir: dir, port: port, links: links, csrf: pairing.NewToken()}
}

const maxMeetingPoints = 5

type setupField struct {
	ID, Error string
}

type setupData struct {
	Title, Saved, CSRF  string
	Mode                string
	HasToken            bool
	PublicURL, Meeting  string
	Running, Standalone bool
	Address, LinkStatus string
	Problems            []setupField
	Errors              map[string]string
	Finished            bool
	Update              updateView
	UpdateNote          string
	UpdateBad           bool
}

func (s *Setup) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if !localOnly(r) {
		http.NotFound(w, r)
		return
	}
	h := w.Header()
	h.Set("Cache-Control", "no-store")
	h.Set("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
	h.Set("X-Frame-Options", "DENY")
	// Not no-referrer: browsers then send "Origin: null" with the form,
	// which sameOrigin refuses (sandboxed pages of other sites send it too).
	h.Set("Referrer-Policy", "same-origin")
	cfg, err := pairing.Load(s.dir)
	if err != nil {
		http.Error(w, "Cannot read the settings: "+err.Error(), http.StatusInternalServerError)
		return
	}
	switch r.Method {
	case http.MethodGet, http.MethodHead:
		d := s.data(cfg)
		switch r.URL.Query().Get("saved") {
		case "1":
			d.Saved = "Saved. " + s.when()
		case "reset":
			d.Saved = "New pairing key saved. " + s.when() + " Then pair each phone again with the 6-digit code."
		}
		if r.URL.Query().Get("update") == "1" {
			s.mu.Lock()
			d.UpdateNote, d.UpdateBad = s.updateNote, s.updateBad
			s.updateNote = ""
			s.mu.Unlock()
		}
		s.render(w, d, http.StatusOK)
	case http.MethodPost:
		if !s.sameOrigin(r) {
			http.Error(w, "Refused: this form only works from the page on this "+osutil.Computer()+".", http.StatusForbidden)
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 64<<10)
		if err := r.ParseForm(); err != nil || subtle.ConstantTimeCompare([]byte(r.PostForm.Get("csrf")), []byte(s.csrf)) != 1 {
			http.Error(w, "This page is out of date: open it again.", http.StatusForbidden)
			return
		}
		switch r.PostForm.Get("action") {
		case "reset":
			s.reset(w, r, cfg)
		case "update-check", "update-get", "update-install":
			s.updateAction(w, r, r.PostForm.Get("action"))
		case "done":
			d := s.data(cfg)
			d.Finished = true
			d.Title = "Finished"
			s.render(w, d, http.StatusOK)
			if s.OnDone != nil {
				s.OnDone()
			}
		default:
			s.save(w, r, cfg)
		}
	default:
		w.Header().Set("Allow", "GET, POST")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

// when says when a saved change takes effect.
func (s *Setup) when() string {
	if s.links != nil {
		return "Quit and restart the game for it to take effect."
	}
	return "It takes effect next time you start the game."
}

// sameOrigin refuses form posts from other websites (the page lives on
// 127.0.0.1, which any page in the browser could otherwise post to).
func (s *Setup) sameOrigin(r *http.Request) bool {
	if site := r.Header.Get("Sec-Fetch-Site"); site != "" && site != "same-origin" && site != "none" {
		return false
	}
	o := r.Header.Get("Origin")
	if o == "" {
		return true // the CSRF token still has to match
	}
	for _, host := range []string{"127.0.0.1", "localhost", "[::1]"} {
		if o == fmt.Sprintf("http://%s:%d", host, s.port) {
			return true
		}
	}
	return false
}

func (s *Setup) data(cfg *pairing.Config) setupData {
	d := setupData{
		Title:      "SimSight setup",
		CSRF:       s.csrf,
		Mode:       "quick",
		HasToken:   cfg.TunnelToken != "",
		PublicURL:  cfg.PublicURL,
		Meeting:    strings.Join(cfg.MeetingPoints, "\n"),
		Standalone: s.links == nil,
		Errors:     map[string]string{},
		Update:     s.updateData(),
	}
	switch {
	case cfg.LocalOnly:
		d.Mode = "local"
	case cfg.TunnelToken != "" && cfg.PublicURL != "":
		d.Mode = "fixed"
	}
	if s.links != nil {
		d.Running = true
		link, status := s.links()
		switch {
		case link == "":
			d.LinkStatus = status
		case status == "local":
			d.LinkStatus = "The phone reaches this " + osutil.Computer() + " on the home Wi-Fi only."
			d.Address = addressOf(link)
		default:
			d.LinkStatus = "The phone link is ready."
			d.Address = addressOf(link)
		}
	}
	return d
}

// addressOf is the link without its key (the fragment), safe to show.
func addressOf(link string) string {
	u, err := url.Parse(link)
	if err != nil {
		return ""
	}
	return u.Scheme + "://" + u.Host
}

func (s *Setup) save(w http.ResponseWriter, r *http.Request, cfg *pairing.Config) {
	f := r.PostForm
	d := s.data(cfg)
	d.Mode = f.Get("mode")
	d.PublicURL = strings.TrimSpace(f.Get("public_url"))
	d.Meeting = strings.TrimSpace(f.Get("meeting_points"))
	problem := func(id, msg string) {
		d.Errors[id] = msg
		d.Problems = append(d.Problems, setupField{id, msg})
	}

	next := *cfg
	switch d.Mode {
	case "quick", "local":
		// Another choice forgets the fixed address (said on the page).
		next.TunnelToken, next.PublicURL = "", ""
		next.LocalOnly = d.Mode == "local"
	case "fixed":
		next.LocalOnly = false
		if raw := f.Get("tunnel_token"); strings.TrimSpace(raw) != "" {
			tok, err := TunnelToken(raw)
			if err != nil {
				problem("tunnel_token", err.Error())
			}
			next.TunnelToken = tok
		} else if cfg.TunnelToken == "" {
			problem("tunnel_token", "Paste the tunnel token from the Cloudflare dashboard.")
		}
		if d.PublicURL == "" {
			problem("public_url", "Type the public address of the tunnel, for example sims.example.com.")
		} else if u, err := WebAddress(d.PublicURL); err != nil {
			problem("public_url", err.Error())
		} else {
			next.PublicURL = u
		}
	default:
		problem("mode-quick", "Choose how the phone reaches this "+osutil.Computer()+".")
	}
	next.MeetingPoints = nil
	for _, line := range strings.FieldsFunc(d.Meeting, func(r rune) bool { return r == '\n' || r == '\r' || r == ',' }) {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if d.Mode == "local" {
			problem("meeting_points", "Meeting points need the internet: choose a changing or fixed address, or leave the meeting points empty.")
			break
		}
		u, err := WebAddress(line)
		if err != nil {
			problem("meeting_points", line+": "+err.Error())
			break
		}
		if !slices.Contains(next.MeetingPoints, u) {
			next.MeetingPoints = append(next.MeetingPoints, u)
		}
	}
	if len(next.MeetingPoints) > maxMeetingPoints {
		problem("meeting_points", fmt.Sprintf("At most %d meeting points.", maxMeetingPoints))
	}
	if len(d.Problems) > 0 {
		d.Title = fmt.Sprintf("%d %s", len(d.Problems), plural(len(d.Problems), "problem", "problems"))
		s.render(w, d, http.StatusUnprocessableEntity)
		return
	}
	if len(next.MeetingPoints) > 0 && (next.MacID == "" || next.MacSecret == "") {
		next.MacID, next.MacSecret = pairing.NewToken(), pairing.NewToken()
	}
	if err := next.Save(s.dir); err != nil {
		http.Error(w, "Cannot save the settings: "+err.Error(), http.StatusInternalServerError)
		return
	}
	slog.Info("settings saved from the setup page", "mode", d.Mode, "meeting_points", len(next.MeetingPoints))
	http.Redirect(w, r, "/setup?saved=1", http.StatusSeeOther)
}

func (s *Setup) reset(w http.ResponseWriter, r *http.Request, cfg *pairing.Config) {
	if r.PostForm.Get("confirm") != "yes" {
		d := s.data(cfg)
		d.Errors["confirm"] = "Check the box first: every phone will need to pair again."
		d.Problems = []setupField{{"confirm", d.Errors["confirm"]}}
		d.Title = "1 problem"
		s.render(w, d, http.StatusUnprocessableEntity)
		return
	}
	cfg.Token = pairing.NewToken()
	if err := cfg.Save(s.dir); err != nil {
		http.Error(w, "Cannot save the settings: "+err.Error(), http.StatusInternalServerError)
		return
	}
	slog.Info("pairing key replaced from the setup page")
	http.Redirect(w, r, "/setup?saved=reset", http.StatusSeeOther)
}

func plural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}

// TunnelToken checks a Cloudflare tunnel token. People often paste the whole
// "cloudflared service install eyJ…" line from the dashboard: the token is
// the part that starts with eyJ.
func TunnelToken(raw string) (string, error) {
	tok := ""
	for _, word := range strings.Fields(raw) {
		if strings.HasPrefix(word, "eyJ") {
			tok = strings.Trim(word, `"'`)
		}
	}
	bad := fmt.Errorf("This is not a Cloudflare tunnel token. It is the long text starting with eyJ, in the Cloudflare dashboard under Networks, Tunnels, your tunnel, Install connector.")
	if tok == "" {
		return "", bad
	}
	b, err := base64.StdEncoding.DecodeString(tok)
	if err != nil {
		if b, err = base64.RawStdEncoding.DecodeString(strings.TrimRight(tok, "=")); err != nil {
			return "", bad
		}
	}
	var t struct{ A, T, S string }
	if json.Unmarshal(b, &t) != nil || t.A == "" || t.T == "" || t.S == "" {
		return "", bad
	}
	return tok, nil
}

// WebAddress normalizes "sims.example.com" or "https://sims.example.com/" to
// "https://sims.example.com". http is accepted for this Mac only (testing).
func WebAddress(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	if !strings.Contains(raw, "://") {
		raw = "https://" + raw
	}
	u, err := url.Parse(raw)
	bad := fmt.Errorf("This is not a web address. Type it like sims.example.com.")
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || strings.Trim(u.Path, "/") != "" {
		return "", bad
	}
	local := u.Hostname() == "localhost" || u.Hostname() == "127.0.0.1"
	switch {
	case u.Scheme == "https" && (strings.Contains(u.Hostname(), ".") || local):
	case u.Scheme == "http" && local:
	case u.Scheme == "http":
		return "", fmt.Errorf("The address must start with https, not http.")
	default:
		return "", bad
	}
	return u.Scheme + "://" + strings.ToLower(u.Host), nil
}

func (s *Setup) render(w http.ResponseWriter, d setupData, status int) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	if err := setupTmpl.Execute(w, d); err != nil {
		slog.Warn("setup page", "err", err)
	}
}

// The page names this computer ("this Mac", "this PC") and its menu keys.
var setupTmpl = template.Must(template.New("setup").Parse(localize(`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{if ne .Title "SimSight setup"}}{{.Title}}: {{end}}SimSight setup</title>
<style>
:root{--bg:#f6faf7;--fg:#10261b;--muted:#3d5649;--card:#fff;--line:#7f9a8b;--accent:#0b6b3a;--accent-fg:#fff;--bad:#a4161a;--bad-bg:#fdecec;--ok-bg:#e3f4ea;--focus:#1a4fd6}
@media (prefers-color-scheme:dark){:root{--bg:#0f2a1f;--fg:#eef7f1;--muted:#b5d3c2;--card:#16382a;--line:#6f917f;--accent:#7fe0a8;--accent-fg:#06200f;--bad:#ffb3b3;--bad-bg:#3a1416;--ok-bg:#173f2b;--focus:#9ec1ff}}
*{box-sizing:border-box}
body{font:18px/1.55 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0}
main{max-width:40rem;margin:0 auto;padding:1.5rem 1rem 4rem}
h1{font-size:1.8rem;margin:.5rem 0 1rem}
h2{font-size:1.3rem;margin:2.2rem 0 .6rem}
section,.note{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:1rem 1.2rem;margin:1rem 0}
section>:first-child,.note>:first-child{margin-top:0}
section>:last-child,.note>:last-child{margin-bottom:0}
fieldset{border:0;margin:0;padding:0}
legend{font-weight:700;padding:0;margin-bottom:.5rem}
.choice{display:grid;grid-template-columns:auto 1fr;gap:.2rem .7rem;align-items:start;padding:.6rem 0}
.choice input{width:1.5rem;height:1.5rem;margin:.15rem 0 0;accent-color:var(--accent)}
.choice label{font-weight:600}
.hint{color:var(--muted);margin:.1rem 0 0;grid-column:2}
.field{margin:1.2rem 0}
.field label{display:block;font-weight:600}
.field .hint{margin:.1rem 0 .4rem}
input[type=text],input[type=url],textarea{width:100%;font:inherit;min-height:2.9rem;padding:.4rem .7rem;border:2px solid var(--line);border-radius:10px;background:var(--bg);color:var(--fg)}
input[aria-invalid=true]{border-color:var(--bad)}
.error{color:var(--bad);font-weight:700;margin:.3rem 0}
.check{display:flex;gap:.7rem;align-items:flex-start;margin:1rem 0}
.check input{width:1.5rem;height:1.5rem;flex:none;margin-top:.15rem}
button{font:inherit;font-weight:700;min-height:2.9rem;padding:.5rem 1.3rem;border-radius:10px;border:2px solid var(--accent);background:var(--accent);color:var(--accent-fg);cursor:pointer}
button.secondary{background:transparent;color:var(--fg);border-color:var(--line)}
a{color:inherit;text-underline-offset:.2em}
:focus-visible{outline:3px solid var(--focus);outline-offset:3px}
.saved{background:var(--ok-bg);border-color:var(--accent)}
.problems{background:var(--bad-bg);border:2px solid var(--bad)}
.problems h2{margin-top:0}
code{font-size:.9em;word-break:break-all}
</style></head><body><main>
<h1>SimSight setup</h1>
{{if .Finished}}
<p role="status" class="note saved">Finished. You can close this browser tab.</p>
{{else}}
{{if .Saved}}<p role="status" class="note saved">{{.Saved}}</p>{{end}}
{{if .UpdateNote}}<p {{if .UpdateBad}}role="alert" class="note problems"{{else}}role="status" class="note saved"{{end}}>{{.UpdateNote}}</p>{{end}}
{{if .Problems}}<div class="note problems" role="alert">
<h2>{{len .Problems}} {{if eq (len .Problems) 1}}problem{{else}}problems{{end}}, nothing was saved</h2>
<ul>{{range .Problems}}<li><a href="#{{.ID}}">{{.Error}}</a></li>{{end}}</ul>
</div>{{end}}

<h2>Right now</h2>
<section>
{{if .Running}}<p>The game is running. {{.LinkStatus}}</p>
{{if .Address}}<p>Phone address: <a href="{{.Address}}"><code>{{.Address}}</code></a></p>{{end}}
<p><a href="/pair">Pair a phone</a>: shows the 6-digit code and the QR code.</p>
{{else}}<p>The game is not running. Your choices here take effect when it starts.</p>
<p>To pair a phone, start the game, load a household, press Option Command A, then choose phone companion.</p>{{end}}
</section>

{{with .Update}}{{if .Shown}}
<h2 id="update">SimSight version</h2>
<section>
<p>{{if .Released}}Installed: SimSight {{.Version}}.{{else}}Installed: built from the source code ({{.Version}}).{{end}}{{if .Latest}} Latest release: {{.Latest}}.{{end}}</p>
{{if .Ready}}
{{if $.Running}}<p>Update {{.Ready}} is downloaded. It is installed when you quit the game; the next game start uses it.</p>
{{else}}{{if .LastProblem}}<p class="error">The last install did not finish: {{.LastProblem}}</p>{{end}}
<form method="post" action="/setup"><input type="hidden" name="csrf" value="{{$.CSRF}}">
<p class="hint" id="hint-install">It backs up your game settings and saves first, like the installer. Takes a minute.</p>
<p><button type="submit" name="action" value="update-install" aria-describedby="hint-install">Install SimSight {{.Ready}} now</button></p></form>{{end}}
{{else if .Newer}}
<form method="post" action="/setup"><input type="hidden" name="csrf" value="{{$.CSRF}}">
<p class="hint" id="hint-get">{{if $.Running}}Downloads it now; it is installed when you quit the game.{{else}}Downloads and installs it. It backs up your game settings and saves first, like the installer. Takes a minute or two.{{end}}</p>
<p><button type="submit" name="action" value="update-get" aria-describedby="hint-get">{{if $.Running}}Download SimSight {{.Latest}}{{else}}Download and install SimSight {{.Latest}}{{end}}</button></p></form>
{{else}}
<form method="post" action="/setup"><input type="hidden" name="csrf" value="{{$.CSRF}}">
<p><button type="submit" name="action" value="update-check" class="secondary">Check for updates</button></p></form>
{{end}}
</section>
{{end}}{{end}}

<form method="post" action="/setup">
<input type="hidden" name="csrf" value="{{.CSRF}}">
<h2>How the phone reaches this Mac</h2>
<section>
<fieldset>
<legend>Phone address</legend>
<div class="choice">
<input type="radio" id="mode-quick" name="mode" value="quick" aria-describedby="hint-quick"{{if eq .Mode "quick"}} checked{{end}}>
<label for="mode-quick">Changing address (default)</label>
<p class="hint" id="hint-quick">Works from any network, nothing to set up. The address changes each game session, so the phone opens the new link (QR code) each time.</p>
</div>
<div class="choice">
<input type="radio" id="mode-fixed" name="mode" value="fixed" aria-describedby="hint-fixed"{{if eq .Mode "fixed"}} checked{{end}}>
<label for="mode-fixed">Fixed address, with your own Cloudflare tunnel</label>
<p class="hint" id="hint-fixed">The same address every session: add the app to the Home Screen once. Needs a domain on Cloudflare; fill in the two fields below.</p>
</div>
<div class="choice">
<input type="radio" id="mode-local" name="mode" value="local" aria-describedby="hint-local"{{if eq .Mode "local"}} checked{{end}}>
<label for="mode-local">Home Wi-Fi only</label>
<p class="hint" id="hint-local">Nothing goes through the internet. The phone must be on the same Wi-Fi as this Mac, and some home networks block this.</p>
</div>
</fieldset>
<p class="hint">Choosing changing address or Wi-Fi only forgets a saved tunnel token.</p>

<div class="field">
<label for="tunnel_token">Tunnel token (fixed address only)</label>
<p class="hint" id="hint-token">{{if .HasToken}}A token is saved. Leave this empty to keep it, or paste a new one.{{else}}In the Cloudflare dashboard: Networks, Tunnels, your tunnel, Install connector. Paste the whole command or only the long text starting with eyJ.{{end}}</p>
{{with index .Errors "tunnel_token"}}<p class="error" id="err-token">{{.}}</p>{{end}}
<input type="text" id="tunnel_token" name="tunnel_token" autocomplete="off" autocapitalize="off" spellcheck="false" aria-describedby="hint-token{{if index .Errors "tunnel_token"}} err-token{{end}}"{{if index .Errors "tunnel_token"}} aria-invalid="true"{{end}}>
</div>
<div class="field">
<label for="public_url">Public address (fixed address only)</label>
<p class="hint" id="hint-url">The public hostname you gave the tunnel, for example sims.example.com.</p>
{{with index .Errors "public_url"}}<p class="error" id="err-url">{{.}}</p>{{end}}
<input type="text" inputmode="url" id="public_url" name="public_url" value="{{.PublicURL}}" autocomplete="off" autocapitalize="off" spellcheck="false" aria-describedby="hint-url{{if index .Errors "public_url"}} err-url{{end}}"{{if index .Errors "public_url"}} aria-invalid="true"{{end}}>
</div>
</section>

<h2>Meeting points (optional)</h2>
<section>
<div class="field">
<label for="meeting_points">Meeting point addresses, one per line</label>
<p class="hint" id="hint-meet">A SimSight meeting point gives the phone app one address for good: the app opens there and finds this Mac by itself each session. Use your own, or a shared one someone runs. With several, this Mac announces itself to each, so the app keeps working if one stops. Pairing codes then have 9 digits. Leave empty if you have none; everything else keeps working.</p>
{{with index .Errors "meeting_points"}}<p class="error" id="err-meet">{{.}}</p>{{end}}
<textarea id="meeting_points" name="meeting_points" rows="3" autocomplete="off" autocapitalize="off" spellcheck="false" aria-describedby="hint-meet{{if index .Errors "meeting_points"}} err-meet{{end}}"{{if index .Errors "meeting_points"}} aria-invalid="true"{{end}}>{{.Meeting}}</textarea>
</div>
</section>
<p><button type="submit" name="action" value="save">Save</button></p>
</form>

<h2>Disconnect every phone</h2>
<form method="post" action="/setup">
<input type="hidden" name="csrf" value="{{.CSRF}}">
<section>
<p id="hint-reset">Makes a new pairing key. Every phone that was paired stops working until it pairs again with the 6-digit code. Use it if a phone was lost or you shared a link by mistake.</p>
{{with index .Errors "confirm"}}<p class="error" id="err-confirm">{{.}}</p>{{end}}
<div class="check"><input type="checkbox" id="confirm" name="confirm" value="yes" aria-describedby="hint-reset{{if index .Errors "confirm"}} err-confirm{{end}}"{{if index .Errors "confirm"}} aria-invalid="true"{{end}}>
<label for="confirm">Yes, every phone will pair again</label></div>
<p><button type="submit" name="action" value="reset" class="secondary">Disconnect every phone</button></p>
</section>
</form>

{{if .Standalone}}<form method="post" action="/setup">
<input type="hidden" name="csrf" value="{{.CSRF}}">
<p><button type="submit" name="action" value="done" class="secondary">Done</button></p>
</form>{{end}}
{{end}}
</main></body></html>`)))

func localize(page string) string {
	return strings.NewReplacer("this Mac", "this "+osutil.Computer(), "Option Command A", osutil.MenuKeys()).Replace(page)
}
