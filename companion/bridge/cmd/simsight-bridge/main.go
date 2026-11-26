// Command simsight-bridge connects The Sims 4 (SimSight mod) to phones.
//
// The mod starts it with pipes: speech and game state arrive on stdin,
// phone commands leave on stdout. It serves the phone app on the local
// network and exits when the game closes the pipe.
//
//	simsight-bridge --speaker /path/simsight-say [--rate 3 --volume 70 --voice Thomas --lang fr-FR]
//	simsight-bridge --speaker none   # Windows: the screen reader speaks, through the mod
//	simsight-bridge --demo   # fake game, for developing the phone app
//	simsight-bridge link     # print the phone link and its QR code
//	simsight-bridge setup    # open the settings page (game running or not)
//	simsight-bridge version  # this build's release tag, or "dev"
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	v1 "github.com/the40n8/simsight-companion/companion/bridge/gen/simsight/v1"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/announce"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/demo"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/hub"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/meeting"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/osutil"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/pairing"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/pipe"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/server"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/speaker"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/tunnel"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/update"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/webui"
)

// version is the release tag, set by the release build (-X main.version);
// "dev" when built from the source.
var version = "dev"

// updater is this build's update checker, for the setup page. The Sims 4
// user folder holds this program's folder: <user>/Mods/SimSight/simsight-bridge.
func updater(dataDir, channel string) *update.Updater {
	u := update.New(version, dataDir)
	u.SetChannel(channel)
	u.UserDir = filepath.Dir(filepath.Dir(filepath.Dir(osutil.NextToMe("simsight-bridge"))))
	return u
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "link" {
		if err := printLink(os.Args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if len(os.Args) > 1 && os.Args[1] == "version" {
		fmt.Println(version)
		return
	}
	if len(os.Args) > 1 && os.Args[1] == "setup" {
		if err := runSetup(os.Args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if err := run(); err != nil {
		slog.Error("bridge stopped", "err", err)
		os.Exit(1)
	}
}

func run() error {
	var (
		speakerPath = flag.String("speaker", "", `path to simsight-say (default: next to this binary); "none": no voice of its own, announcements go to the mod (Windows)`)
		rate        = flag.Int("rate", 3, "voice rate -10..10")
		volume      = flag.Int("volume", 70, "voice volume 0..100")
		voiceName   = flag.String("voice", "", "part of a macOS voice name")
		lang        = flag.String("lang", "", "voice language, e.g. fr-FR")
		dataDir     = flag.String("data", osutil.DataDir(), "pairing and log folder")
		webDir      = flag.String("web", "", "serve the phone app from this folder instead of the embedded build (development)")
		port        = flag.Int("port", 0, "listen port (default: saved port, 47800)")
		demoMode    = flag.Bool("demo", false, "run a fake game instead of reading the mod's pipe")
		useTunnel   = flag.Bool("tunnel", true, "open a Cloudflare quick tunnel for the phone when cloudflared is available")
	)
	flag.Parse()

	if err := os.MkdirAll(*dataDir, 0o700); err != nil {
		return err
	}
	// stdout is the pipe to the game: logs go to a file (and stderr in demo).
	logFile, err := os.OpenFile(filepath.Join(*dataDir, "bridge.log"), os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	defer logFile.Close()
	var logOut = logFile
	if *demoMode {
		logOut = os.Stderr
	}
	slog.SetDefault(slog.New(slog.NewTextHandler(logOut, nil)))

	cfg, err := pairing.Load(*dataDir)
	if err != nil {
		return err
	}
	if *port != 0 {
		cfg.Port = *port
	}
	linkFile, err := cfg.WriteLinkFile(*dataDir)
	if err != nil {
		return err
	}
	slog.Info("phone link written", "file", linkFile)
	if *demoMode {
		for _, l := range cfg.Links() {
			fmt.Fprintln(os.Stderr, "phone link:", l)
		}
	}

	var web fs.FS = webui.FS()
	if *webDir != "" {
		web = os.DirFS(*webDir)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	h := hub.New(nil)
	h.SetPlatform(osutil.Platform(), osutil.ComputerName())
	// What the Mac voice announces: kept in the data folder, set from the phone.
	h.SetMacAnnounceSaver(func(a *v1.MacAnnounce) error { return announce.Save(*dataDir, a) })
	_ = h.SetMacAnnounce(announce.Load(*dataDir))

	// Phone link: the Cloudflare tunnel when it is up (reachable from any
	// network), else the Mac's local address. Shown at http://127.0.0.1:<port>/pair.
	var tun *tunnel.Tunnel
	// The tunnel stops before an update replaces cloudflared.
	tunCtx, stopTunnel := context.WithCancel(ctx)
	defer stopTunnel()
	tunDone := make(chan struct{})
	var onTunnelReady atomic.Pointer[func(url string)] // set once the speaker exists
	// Optional meeting points: the phone app at one fixed address, which
	// finds this Mac's tunnel by itself (docs/mac/MEETING-POINT.md). This Mac
	// announces itself to each; one that cannot be reached is skipped.
	var meets []*meeting.Client
	for _, base := range cfg.MeetingPoints {
		m := meeting.New(base, cfg.MacID, cfg.MacSecret)
		go m.Run(ctx)
		meets = append(meets, m)
		slog.Info("meeting point", "url", base)
	}
	links := func() (string, string) {
		if tun != nil {
			if url, status := tun.URL(); url != "" {
				if len(meets) > 0 {
					return meets[0].AppLink(cfg.Token), "ready"
				}
				return url + "/#k=" + cfg.Token, "ready"
			} else if status == "starting" {
				return "", "Opening a Cloudflare tunnel for the phone… this page refreshes by itself."
			}
		}
		if l := cfg.Links(); len(l) > 0 {
			return l[0], "local"
		}
		return "", "No network address: is the Mac on Wi-Fi?"
	}
	if cfg.LocalOnly {
		*useTunnel = false
		slog.Info("home Wi-Fi only (setup page): no tunnel")
	}
	if bin := tunnel.Find(); *useTunnel && bin != "" {
		ready := func(url string) {
			link := url + "/#k=" + cfg.Token
			path := filepath.Join(*dataDir, "phone-link.txt")
			_ = os.WriteFile(path, []byte(link+"\n"+strings.Join(cfg.Links(), "\n")+"\n"), 0o600)
			for _, m := range meets {
				m.SetURL(url)
			}
			if f := onTunnelReady.Load(); f != nil {
				(*f)(url)
			}
		}
		if cfg.TunnelToken != "" && cfg.PublicURL != "" {
			slog.Info("fixed phone address", "url", cfg.PublicURL)
			tun = tunnel.NewNamed(bin, cfg.TunnelToken, cfg.PublicURL, cfg.Port, ready)
		} else {
			tun = tunnel.New(bin, cfg.Port, ready)
		}
		go func() { tun.Run(tunCtx); close(tunDone) }()
	} else if *useTunnel {
		slog.Info("no cloudflared: phone link on the local network only")
	}
	// Pairing code, said by the Mac voice when the phone link page opens.
	codes := server.NewCodes(cfg.Token)
	var cors []string
	if len(meets) > 0 {
		// Many computers' codes side by side at a shared meeting point.
		codes.SetDigits(9)
		codes.OnNew(func(code string, until time.Time) {
			for _, m := range meets {
				m.SetCode(code, until)
			}
		})
		for _, m := range meets {
			cors = append(cors, m.Origin())
		}
	}
	if *demoMode {
		codes.SetSay(func(code string) { fmt.Fprintln(os.Stderr, "pairing code:", code) })
	}
	// All networks, so a phone on the same Wi-Fi can connect. On Windows,
	// only this PC while the tunnel carries the phone: listening on the
	// network makes Windows show a firewall question over the game.
	listen := ":" + strconv.Itoa(cfg.Port)
	if runtime.GOOS == "windows" && tun != nil {
		listen = "127.0.0.1:" + strconv.Itoa(cfg.Port)
	}
	upd := updater(*dataDir, cfg.UpdateChannel)
	update.Cleanup()
	setup := server.NewSetup(*dataDir, cfg.Port, links)
	setup.Updates = upd
	ln, err := net.Listen("tcp", listen)
	if err != nil {
		// Another game session or bridge holds the port: keep speech working.
		slog.Error("phone server unavailable", "port", cfg.Port, "err", err)
	} else {
		srv := &http.Server{
			Handler:           server.Handler(h, cfg.Token, web, links, codes, cors, setup),
			ReadHeaderTimeout: 10 * time.Second,
		}
		go func() {
			if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
				slog.Error("phone server stopped", "err", err)
			}
		}()
		defer func() {
			sctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			_ = srv.Shutdown(sctx)
		}()
		slog.Info("phone server listening", "port", cfg.Port)
	}

	if *demoMode {
		demo.Run(ctx, h)
		return nil
	}

	if *speakerPath == "" {
		*speakerPath = osutil.NextToMe("simsight-say")
		if runtime.GOOS == "windows" {
			*speakerPath = "none"
		}
	}
	// say: the bridge's own announcements. With a speaker (Mac) its voice;
	// without (Windows) the mod's screen reader, through the pipe.
	var spk *speaker.Speaker
	var say func(text string)
	if *speakerPath == "none" {
		say = func(text string) {
			go func() {
				sctx, cancel := context.WithTimeout(ctx, hub.CommandTimeout)
				defer cancel()
				h.Command(sctx, &v1.ToGame{Command: &v1.ToGame_Say{Say: &v1.SayRequest{Text: text}}})
			}()
		}
	} else {
		spk = speaker.New(*speakerPath, &v1.Voice{Rate: int32(*rate), Volume: int32(*volume), Name: *voiceName, Lang: *lang})
		say = func(text string) { spk.Line("Q" + url.PathEscape(text)) }
	}
	// Said once, when the phone link works and a household is loaded (the
	// accessibility menu only opens in a household): how to show the QR code.
	var linkReady, told atomic.Bool
	tell := func() {
		if linkReady.Load() && h.State().GetInGame() && told.CompareAndSwap(false, true) {
			say("Phone link ready. Press " + osutil.MenuKeys() + " for the accessibility menu, then choose phone companion.")
		}
	}
	// Updates: said once in a household. Just installed, or a newer
	// release found by a quiet check a minute after the game starts.
	var updateNews atomic.Pointer[string]
	if tag := upd.JustInstalled(); tag != "" {
		news := "SimSight updated to version " + strings.TrimPrefix(tag, "v") + "."
		updateNews.Store(&news)
	} else if upd.Released() {
		go func() {
			select {
			case <-ctx.Done():
				return
			case <-time.After(time.Minute):
			}
			cctx, cancel := context.WithTimeout(ctx, 30*time.Second)
			defer cancel()
			rel, err := upd.Check(cctx)
			if err != nil || !update.Newer(version, rel.Tag) {
				return
			}
			slog.Info("update available", "tag", rel.Tag)
			news := "SimSight version " + strings.TrimPrefix(rel.Tag, "v") + " is available. To install it, choose companion setup in the accessibility menu."
			updateNews.Store(&news)
		}()
	}
	sayNews := func() {
		if h.State().GetInGame() {
			if news := updateNews.Swap(nil); news != nil {
				say(*news)
			}
		}
	}
	linkUp := func(string) {
		linkReady.Store(true)
		tell()
	}
	onTunnelReady.Store(&linkUp)
	codes.SetSay(func(code string) {
		say("Pairing code: " + server.SpokenCode(code) + ". Again: " + server.SpokenCode(code) + ".")
	})
	go func() {
		// Only new events: Subscribe replays recent ones first.
		since := h.LastEventSeq()
		for msg := range h.Subscribe(ctx) {
			if msg.GetState().GetInGame() {
				tell()
				sayNews()
			}
			e := msg.GetEvent()
			if e == nil || e.GetSeq() <= since {
				continue
			}
			// The Mac voice off (Option+Command+S) silences these too.
			if a := h.MacAnnounce(); a != nil && h.State().GetNarration().GetMacVoice() && announce.Wants(a, e) {
				if text := announce.Text(e); text != "" {
					say(text)
				}
			}
		}
	}()
	defer spk.Close()

	p := pipe.New(os.Stdin, os.Stdout)
	h.SetSender(p.Send)
	h.SetConnected(true)
	defer h.SetConnected(false)
	keyQueue := make(chan *v1.KeyEvent, 64)
	go func() {
		for ev := range keyQueue {
			kctx, cancel := context.WithTimeout(ctx, hub.CommandTimeout)
			h.Command(kctx, &v1.ToGame{Command: &v1.ToGame_Key{Key: ev}})
			cancel()
		}
	}()
	pairPage := "http://127.0.0.1:" + strconv.Itoa(cfg.Port) + "/pair"
	setupPage := "http://127.0.0.1:" + strconv.Itoa(cfg.Port) + "/setup"
	spk.Listen(func(name string) {
		if rest, ok := strings.CutPrefix(name, "key "); ok {
			// "key <family> <vk> <down|up> <shift 0|1>" from the key helper,
			// passed on in order (a key-up must not overtake its key-down).
			if ev := parseKey(rest); ev != nil {
				select {
				case keyQueue <- ev:
				default:
					slog.Warn("key dropped: the game is not answering")
				}
			}
			return
		}
		switch name {
		case "voice":
			go toggleVoice(h)
		case "hub":
			go openMenu(h, "hub")
		case "phone":
			if err := osutil.OpenURL(pairPage); err != nil {
				slog.Warn("open pairing page failed", "err", err)
			}
		case "setup":
			if err := osutil.OpenURL(setupPage); err != nil {
				slog.Warn("open setup page failed", "err", err)
			}
		}
	})

	done := make(chan error, 1)
	go func() {
		done <- p.Run(pipe.Handlers{
			Speech: func(line string) {
				spk.Line(line)
				if text := pipe.SpeechText(line); text != "" {
					h.AddEvent(&v1.Event{Kind: v1.EventKind_EVENT_KIND_SPOKEN, Subject: text})
				}
			},
			Hello:  func(m *v1.Hello) { slog.Info("game connected", "mod", m.GetModVersion(), "game", m.GetGameVersion()) },
			State:  h.SetState,
			Event:  h.AddEvent,
			Result: h.Resolve,
			Voice:  spk.SetVoice,
			Grab:   func(g *v1.GrabKeys) { spk.Grab(g.GetKeys()) },
		})
	}()
	select {
	case err := <-done:
		slog.Info("game closed the pipe", "err", err)
		// A downloaded update: installed now that the game is quitting.
		if tag := upd.Ready(); tag != "" {
			stopTunnel()
			if tun != nil {
				select {
				case <-tunDone:
				case <-time.After(10 * time.Second):
				}
			}
			slog.Info("installing update", "tag", tag)
			ictx, cancel := context.WithTimeout(context.Background(), 15*time.Minute)
			defer cancel()
			if err := upd.Install(ictx, true); err != nil {
				slog.Error("update install failed", "err", err)
			} else {
				slog.Info("update installed", "tag", tag)
			}
		}
		return nil
	case <-ctx.Done():
		return nil
	}
}

// toggleVoice is the Option+Command+S shortcut: the mod's speech on or off
// (the mod itself says "Speech on." or "Speech off.").
func toggleVoice(h *hub.Hub) {
	n := h.State().GetNarration()
	ctx, cancel := context.WithTimeout(context.Background(), hub.CommandTimeout+time.Second)
	defer cancel()
	out := h.Command(ctx, &v1.ToGame{Command: &v1.ToGame_SetNarration{SetNarration: &v1.SetNarrationRequest{
		Narration: &v1.Narration{MacVoice: !n.GetMacVoice(), SimActions: n.GetSimActions()},
	}}})
	if !out.GetOk() {
		slog.Warn("speech toggle failed", "reason", out.GetReason())
	}
}

// parseKey reads "<family> <vk> <down|up> <0|1>" from the key helper.
func parseKey(s string) *v1.KeyEvent {
	f := strings.Fields(s)
	if len(f) != 4 {
		return nil
	}
	vk, err := strconv.Atoi(f[1])
	if err != nil {
		return nil
	}
	return &v1.KeyEvent{Family: f[0], Vk: int32(vk), Down: f[2] == "down", Shift: f[3] == "1"}
}

// openMenu opens one of the mod's menus in the game (Option+Command+A, menu bar).
func openMenu(h *hub.Hub, menu string) {
	ctx, cancel := context.WithTimeout(context.Background(), hub.CommandTimeout+time.Second)
	defer cancel()
	out := h.Command(ctx, &v1.ToGame{Command: &v1.ToGame_OpenMenu{OpenMenu: &v1.OpenMenuRequest{Menu: menu}}})
	if !out.GetOk() {
		slog.Warn("open menu failed", "menu", menu, "reason", out.GetReason())
	}
}

// printLink shows the phone link as text and as a QR code to scan.
func printLink(args []string) error {
	fs := flag.NewFlagSet("link", flag.ExitOnError)
	dataDir := fs.String("data", osutil.DataDir(), "pairing folder")
	all := fs.Bool("all", false, "list every address")
	invert := fs.Bool("invert", false, "QR for terminals with a light background")
	port := fs.Int("port", 0, "link to a different port (e.g. 5173 for the live-reload dev server)")
	base := fs.String("base", "", "public address to link to instead of the Mac's (e.g. a tunnel URL)")
	_ = fs.Parse(args)

	cfg, err := pairing.Load(*dataDir)
	if err != nil {
		return err
	}
	if *port != 0 {
		cfg.Port = *port
	}
	links := cfg.Links()
	if *base != "" {
		links = []string{strings.TrimRight(*base, "/") + "/#k=" + cfg.Token}
	}
	if len(links) == 0 {
		return errors.New("no network address: is the Mac on Wi-Fi?")
	}
	code, err := pairing.TerminalQR(links[0])
	if err != nil {
		return err
	}
	if *invert {
		code = strings.NewReplacer("█", " ", " ", "█", "▀", "▄", "▄", "▀").Replace(code)
	}
	fmt.Print(code)
	fmt.Println(links[0])
	if *all {
		fmt.Println("\nOther addresses, if that one does not open:")
		for _, l := range links[1:] {
			fmt.Println(l)
		}
	}
	return nil
}

// runSetup opens the settings page. With the game running, its bridge
// serves the page; otherwise this serves it on 127.0.0.1 until "Done" is
// pressed (or 30 minutes pass).
func runSetup(args []string) error {
	fs := flag.NewFlagSet("setup", flag.ExitOnError)
	dataDir := fs.String("data", osutil.DataDir(), "pairing folder")
	noOpen := fs.Bool("no-open", false, "print the address without opening the browser")
	_ = fs.Parse(args)

	cfg, err := pairing.Load(*dataDir)
	if err != nil {
		return err
	}
	addr := "127.0.0.1:" + strconv.Itoa(cfg.Port)
	page := "http://" + addr + "/setup"
	open := func() {
		if *noOpen {
			return
		}
		if err := osutil.OpenURL(page); err != nil {
			fmt.Fprintln(os.Stderr, "Open this address in a browser:", page)
		}
	}
	// The game's bridge already listens on this port: it serves the page.
	if c, err := net.DialTimeout("tcp", addr, time.Second); err == nil {
		c.Close()
		fmt.Println("The game is running. Setup page:", page)
		open()
		return nil
	}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return fmt.Errorf("cannot open the setup page on %s: %w", addr, err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 30*time.Minute)
	defer cancel()

	setup := server.NewSetup(*dataDir, cfg.Port, nil)
	setup.Updates = updater(*dataDir, cfg.UpdateChannel)
	update.Cleanup()
	var once atomic.Bool
	setup.OnDone = func() {
		if once.CompareAndSwap(false, true) {
			// Let the "Finished" page reach the browser first.
			time.AfterFunc(500*time.Millisecond, cancel)
		}
	}
	mux := http.NewServeMux()
	mux.Handle("/setup", setup)
	mux.Handle("/", http.RedirectHandler("/setup", http.StatusFound))
	srv := &http.Server{Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	go func() { _ = srv.Serve(ln) }()
	fmt.Println("Setup page open in the browser:", page)
	fmt.Println("Press Done on the page when finished.")
	open()
	<-ctx.Done()
	sctx, scancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer scancel()
	_ = srv.Shutdown(sctx)
	fmt.Println("Setup page closed.")
	return nil
}
