// Package hub keeps the latest game state and recent events, fans them out
// to watching phones, and matches phone commands with the game's results.
package hub

import (
	"context"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	v1 "github.com/the40n8/simsight-companion/companion/bridge/gen/simsight/v1"
	"google.golang.org/protobuf/proto"
)

// recentEvents is how many events a phone gets when it (re)connects.
const recentEvents = 50

// CommandTimeout bounds how long a phone waits for the game to answer.
const CommandTimeout = 5 * time.Second

// Sender delivers a command to the game. It must be safe for concurrent use.
type Sender func(*v1.ToGame) error

type Hub struct {
	mu        sync.Mutex
	state     *v1.GameState
	connected bool
	events    []*v1.Event
	eventSeq  uint64
	subs      map[chan *v1.WatchResponse]struct{}
	// Bridge-side settings shown in every state (not sent by the game).
	macAnnounce *v1.MacAnnounce
	platform    string // "mac" or "windows", copied into every state
	computer    string // this computer's name, copied into every state
	persist     func(*v1.MacAnnounce) error

	send    Sender
	nextID  atomic.Uint64
	pending sync.Map // id -> chan *v1.CommandResult
}

func New(send Sender) *Hub {
	return &Hub{subs: map[chan *v1.WatchResponse]struct{}{}, send: send}
}

// SetSender replaces where commands go (used by demo mode).
func (h *Hub) SetSender(send Sender) {
	h.mu.Lock()
	h.send = send
	h.mu.Unlock()
}

// Subscribe returns a channel that first receives the connection flag, the
// current state and the recent events, then every update. Cancel ctx to
// unsubscribe; the channel is closed afterwards.
func (h *Hub) Subscribe(ctx context.Context) <-chan *v1.WatchResponse {
	ch := make(chan *v1.WatchResponse, 256)
	h.mu.Lock()
	ch <- &v1.WatchResponse{Payload: &v1.WatchResponse_GameConnected{GameConnected: h.connected}}
	if h.state != nil {
		ch <- &v1.WatchResponse{Payload: &v1.WatchResponse_State{State: h.state}}
	}
	for _, e := range h.events {
		ch <- &v1.WatchResponse{Payload: &v1.WatchResponse_Event{Event: e}}
	}
	h.subs[ch] = struct{}{}
	h.mu.Unlock()
	go func() {
		<-ctx.Done()
		h.mu.Lock()
		delete(h.subs, ch)
		h.mu.Unlock()
		close(ch)
	}()
	return ch
}

// broadcast must be called with h.mu held. A phone that cannot keep up is
// skipped for this message rather than blocking the game.
func (h *Hub) broadcast(msg *v1.WatchResponse) {
	for ch := range h.subs {
		select {
		case ch <- msg:
		default:
		}
	}
}

// Connected reports whether a game is attached.
func (h *Hub) Connected() bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.connected
}

func (h *Hub) SetConnected(connected bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.connected == connected {
		return
	}
	h.connected = connected
	h.broadcast(&v1.WatchResponse{Payload: &v1.WatchResponse_GameConnected{GameConnected: connected}})
}

// SetState stores and broadcasts a new state, followed by the events that
// describe what changed (see Diff).
func (h *Hub) SetState(s *v1.GameState) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.state != nil && proto.Equal(h.state, s) {
		return
	}
	if h.macAnnounce != nil {
		s.MacAnnounce = h.macAnnounce
	}
	s.Platform = h.platform
	s.Computer = h.computer
	if h.state != nil && proto.Equal(h.state, s) {
		return
	}
	events := Diff(h.state, s)
	h.state = s
	h.broadcast(&v1.WatchResponse{Payload: &v1.WatchResponse_State{State: s}})
	for _, e := range events {
		h.addEventLocked(e)
	}
}

// SetPlatform names the computer for the phones: its kind ("mac",
// "windows") and its name, in every state from now on.
func (h *Hub) SetPlatform(p, computer string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.platform = p
	h.computer = computer
	if h.state != nil {
		s := proto.Clone(h.state).(*v1.GameState)
		s.Platform = p
		s.Computer = computer
		h.state = s
		h.broadcast(&v1.WatchResponse{Payload: &v1.WatchResponse_State{State: s}})
	}
}

// SetMacAnnounce changes what the Mac voice announces, saves it (when a
// saver is set) and shows it to every phone.
func (h *Hub) SetMacAnnounce(a *v1.MacAnnounce) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.persist != nil {
		if err := h.persist(a); err != nil {
			return err
		}
	}
	h.macAnnounce = a
	if h.state != nil {
		s := proto.Clone(h.state).(*v1.GameState)
		s.MacAnnounce = a
		h.state = s
		h.broadcast(&v1.WatchResponse{Payload: &v1.WatchResponse_State{State: s}})
	}
	return nil
}

// MacAnnounce is the current Mac voice setting (nil before one is set).
func (h *Hub) MacAnnounce() *v1.MacAnnounce {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.macAnnounce
}

// SetMacAnnounceSaver keeps the setting across sessions.
func (h *Hub) SetMacAnnounceSaver(save func(*v1.MacAnnounce) error) {
	h.mu.Lock()
	h.persist = save
	h.mu.Unlock()
}

// LastEventSeq is the sequence number of the newest event (0 if none).
func (h *Hub) LastEventSeq() uint64 {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.eventSeq
}

func (h *Hub) State() *v1.GameState {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.state
}

// AddEvent stamps the event with a sequence number and time, keeps it in
// the recent list and sends it to every phone.
func (h *Hub) AddEvent(e *v1.Event) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.addEventLocked(e)
}

func (h *Hub) addEventLocked(e *v1.Event) {
	h.eventSeq++
	e.Seq = h.eventSeq
	e.At = time.Now().UnixMilli()
	h.events = append(h.events, e)
	if len(h.events) > recentEvents {
		h.events = h.events[len(h.events)-recentEvents:]
	}
	h.broadcast(&v1.WatchResponse{Payload: &v1.WatchResponse_Event{Event: e}})
}

// Command sends cmd to the game and waits for its outcome.
func (h *Hub) Command(ctx context.Context, cmd *v1.ToGame) *v1.CommandOutcome {
	return h.Call(ctx, cmd).GetOutcome()
}

// Call sends cmd to the game and waits for its full result (outcome and
// any data, e.g. targets or interactions). The outcome is never nil.
func (h *Hub) Call(ctx context.Context, cmd *v1.ToGame) *v1.CommandResult {
	failed := func(reason string) *v1.CommandResult {
		return &v1.CommandResult{Outcome: &v1.CommandOutcome{Reason: reason}}
	}
	h.mu.Lock()
	connected, send := h.connected, h.send
	h.mu.Unlock()
	if !connected || send == nil {
		return failed("not_connected")
	}
	cmd.Id = strconv.FormatUint(h.nextID.Add(1), 10)
	done := make(chan *v1.CommandResult, 1)
	h.pending.Store(cmd.Id, done)
	defer h.pending.Delete(cmd.Id)
	if err := send(cmd); err != nil {
		return failed("not_connected")
	}
	ctx, cancel := context.WithTimeout(ctx, CommandTimeout)
	defer cancel()
	select {
	case res := <-done:
		return res
	case <-ctx.Done():
		return failed("timeout")
	}
}

// Resolve delivers the game's answer to a waiting Command.
func (h *Hub) Resolve(r *v1.CommandResult) {
	if ch, ok := h.pending.Load(r.GetId()); ok {
		if r.GetOutcome() == nil {
			r.Outcome = &v1.CommandOutcome{Reason: "no_outcome"}
		}
		ch.(chan *v1.CommandResult) <- r
	}
}
