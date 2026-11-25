// Package meeting tells an optional meeting point (a Worker, the player's own
// or a shared one, companion/meeting-point) where this Mac's phone link is
// today, and which pairing code it shows (hashed there). Nothing else is
// sent: no game data, no pairing key, not even the computer's name (the phone
// learns it from the computer itself).
package meeting

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

// Every so often, so the meeting point knows the Mac is still there.
const refresh = 10 * time.Minute

type Client struct {
	base, id, secret string
	http             *http.Client

	mu       sync.Mutex
	url      string
	code     string
	codeTill time.Time
	poke     chan struct{}
}

func New(base, id, secret string) *Client {
	return &Client{base: strings.TrimRight(base, "/"), id: id, secret: secret,
		http: &http.Client{Timeout: 10 * time.Second}, poke: make(chan struct{}, 1)}
}

// Origin is the meeting point's web origin (for CORS on the phone link).
func (c *Client) Origin() string {
	u, err := url.Parse(c.base)
	if err != nil {
		return ""
	}
	return u.Scheme + "://" + u.Host
}

// AppLink is the phone app at the meeting point, carrying this Mac and its key.
func (c *Client) AppLink(token string) string {
	return c.base + "/#m=" + c.id + "&k=" + token
}

func (c *Client) SetURL(link string) {
	c.mu.Lock()
	c.url = link
	c.mu.Unlock()
	c.wake()
}

func (c *Client) SetCode(code string, until time.Time) {
	c.mu.Lock()
	c.code, c.codeTill = code, until
	c.mu.Unlock()
	c.wake()
}

func (c *Client) wake() {
	select {
	case c.poke <- struct{}{}:
	default:
	}
}

// Run announces the Mac on every change and every few minutes; when ctx
// ends, it says the Mac is gone.
func (c *Client) Run(ctx context.Context) {
	t := time.NewTicker(refresh)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			off, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			c.announce(off, true)
			cancel()
			return
		case <-t.C:
		case <-c.poke:
		}
		c.announce(ctx, false)
	}
}

func (c *Client) announce(ctx context.Context, gone bool) {
	c.mu.Lock()
	body := map[string]any{"id": c.id, "secret": c.secret, "url": c.url}
	if left := time.Until(c.codeTill); c.code != "" && left > 0 {
		body["code"], body["codeSeconds"] = c.code, int(left.Seconds())
	}
	c.mu.Unlock()
	if gone {
		body["url"] = ""
		delete(body, "code")
	}
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/mp/announce", bytes.NewReader(b))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	res, err := c.http.Do(req)
	if err != nil {
		slog.Warn("meeting point unreachable", "err", err)
		return
	}
	res.Body.Close()
	if res.StatusCode != http.StatusOK {
		slog.Warn("meeting point refused this Mac", "status", res.StatusCode)
	}
}
