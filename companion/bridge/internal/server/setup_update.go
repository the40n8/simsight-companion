package server

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"time"

	"github.com/the40n8/simsight-companion/companion/bridge/internal/update"
)

// The "SimSight version" part of the setup page: check for a newer
// release, download it, install it (now with the game closed, or when the
// game quits).

type updateView struct {
	Shown       bool
	Version     string
	Released    bool   // this build is a release, so it can be updated
	Latest      string // the latest release, once checked
	Newer       bool
	Ready       string // a downloaded update waiting to be installed
	LastProblem string // the end of a failed installer run
}

func (s *Setup) updateData() updateView {
	u := s.Updates
	if u == nil {
		return updateView{}
	}
	v := updateView{Shown: true, Version: u.Version, Released: u.Released(), Ready: u.Ready()}
	s.mu.Lock()
	v.Latest = s.latest
	s.mu.Unlock()
	v.Newer = update.Newer(u.Version, v.Latest)
	if v.Ready != "" {
		v.LastProblem = u.LastLog()
	}
	return v
}

func (s *Setup) note(text string, bad bool) {
	s.mu.Lock()
	s.updateNote, s.updateBad = text, bad
	s.mu.Unlock()
}

// updateAction runs "update-check", "update-get" or "update-install", then
// shows the page again with what happened.
func (s *Setup) updateAction(w http.ResponseWriter, r *http.Request, action string) {
	u := s.Updates
	if u == nil {
		http.NotFound(w, r)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Minute)
	defer cancel()
	defer http.Redirect(w, r, "/setup?update=1", http.StatusSeeOther)

	install := func() {
		if s.links != nil {
			s.note("Update "+u.Ready()+" downloaded. It is installed when you quit the game, and the next game start uses it.", false)
			return
		}
		tag := u.Ready()
		if err := u.Install(ctx, false); err != nil {
			slog.Warn("update install failed", "err", err)
			s.note("The update was not installed: "+err.Error(), true)
			return
		}
		slog.Info("update installed", "tag", tag)
		s.note("SimSight "+tag+" is installed. Start the game to use it.", false)
	}

	switch action {
	case "update-install":
		if u.Ready() == "" {
			s.note("No update is downloaded.", true)
			return
		}
		install()
		return
	}
	rel, err := u.Check(ctx)
	if errors.Is(err, update.ErrNoRelease) {
		s.note("No SimSight release is published yet: nothing to update.", false)
		return
	}
	if err != nil {
		s.note("Could not check for updates: "+err.Error()+". Check the internet connection and try again.", true)
		return
	}
	s.mu.Lock()
	s.latest = rel.Tag
	s.mu.Unlock()
	if !update.Newer(u.Version, rel.Tag) {
		if u.Released() {
			s.note("SimSight is up to date ("+u.Version+").", false)
		} else {
			s.note("The latest release is "+rel.Tag+". This SimSight was built from the source code, so it is not updated from here.", false)
		}
		return
	}
	if action == "update-check" {
		s.note("SimSight "+rel.Tag+" is available.", false)
		return
	}
	if u.Ready() != rel.Tag {
		if err := u.Download(ctx, rel); err != nil {
			slog.Warn("update download failed", "err", err)
			s.note("The update was not downloaded: "+err.Error()+".", true)
			return
		}
	}
	install()
}
