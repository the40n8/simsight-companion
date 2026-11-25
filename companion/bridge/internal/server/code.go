package server

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"math/big"
	"net/http"
	"strings"
	"sync"
	"time"
)

// Pairing by code: when the phone link is shown on the Mac, the Mac also says
// a 6-digit code (9 digits with meeting points, where many computers' codes
// live side by side); typed in the phone app, it gives the app the pairing key.
// No camera, no copy and paste. A code is made on this Mac only, works once,
// for a few minutes, and only for a few wrong tries.
const (
	CodeTTL      = 5 * time.Minute
	CodeMaxTries = 5
)

type Codes struct {
	token  string
	now    func() time.Time
	digits int

	mu      sync.Mutex
	code    string
	expires time.Time
	tries   int
	say     func(code string)
	onNew   func(code string, until time.Time)
}

func NewCodes(token string) *Codes {
	return &Codes{token: token, now: time.Now, digits: 6}
}

// SetDigits sets the code length: 6, or 9 with meeting points.
func (c *Codes) SetDigits(n int) {
	c.mu.Lock()
	c.digits = n
	c.mu.Unlock()
}

// Digits is the code length the phone app waits for.
func (c *Codes) Digits() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.digits
}

// SetSay sets how a code is announced on the Mac (the Mac voice).
func (c *Codes) SetSay(say func(code string)) {
	c.mu.Lock()
	c.say = say
	c.mu.Unlock()
}

// OnNew is told each new code (the meeting point keeps it, hashed).
func (c *Codes) OnNew(f func(code string, until time.Time)) {
	c.mu.Lock()
	c.onNew = f
	c.mu.Unlock()
}

// Show returns the current code, making a new one when there is none; it is
// announced when new, or when announce is true (the page opened again).
func (c *Codes) Show(announce bool) string {
	c.mu.Lock()
	fresh := false
	if c.code == "" || !c.now().Before(c.expires) || c.tries >= CodeMaxTries {
		limit := big.NewInt(1)
		for range c.digits {
			limit.Mul(limit, big.NewInt(10))
		}
		n, err := rand.Int(rand.Reader, limit)
		if err != nil {
			c.mu.Unlock()
			return ""
		}
		c.code = fmt.Sprintf("%0*d", c.digits, n.Int64())
		c.expires = c.now().Add(CodeTTL)
		c.tries = 0
		fresh = true
	}
	code, say, onNew, until := c.code, c.say, c.onNew, c.expires
	c.mu.Unlock()
	if fresh && onNew != nil {
		onNew(code, until)
	}
	if say != nil && (fresh || announce) {
		say(code)
	}
	return code
}

// Check uses up the code when it matches; wrong tries count against it.
// ok is false with tooMany true once the code is used up by wrong tries.
func (c *Codes) Check(code string) (ok, tooMany bool) {
	code = strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, code)
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.code == "" || !c.now().Before(c.expires) {
		return false, false
	}
	if c.tries >= CodeMaxTries {
		return false, true
	}
	if len(code) == len(c.code) && subtle.ConstantTimeCompare([]byte(code), []byte(c.code)) == 1 {
		c.code = "" // once only
		return true, false
	}
	c.tries++
	return false, c.tries >= CodeMaxTries
}

// Handler answers POST {"code":"123456"} with {"token":"..."}; 403 for a wrong
// or expired code, 429 once there were too many wrong tries. GET says how
// many digits the code has ({"digits":6}).
func (c *Codes) Handler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		if r.Method == http.MethodGet {
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]int{"digits": c.Digits()})
			return
		}
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var body struct {
			Code string `json:"code"`
		}
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 256)).Decode(&body); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		ok, tooMany := c.Check(body.Code)
		switch {
		case ok:
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]string{"token": c.token})
		case tooMany:
			w.WriteHeader(http.StatusTooManyRequests)
		default:
			time.Sleep(300 * time.Millisecond) // slows guessing a little more
			w.WriteHeader(http.StatusForbidden)
		}
	}
}

// Spoken digit by digit, so the Mac voice does not read "four hundred…";
// a 9-digit code in three groups: "4 8 2, 9 1 3, 5 5 7".
func SpokenCode(code string) string {
	digits := strings.Split(code, "")
	if len(digits) != 9 {
		return strings.Join(digits, " ")
	}
	groups := []string{strings.Join(digits[0:3], " "), strings.Join(digits[3:6], " "), strings.Join(digits[6:9], " ")}
	return strings.Join(groups, ", ")
}
