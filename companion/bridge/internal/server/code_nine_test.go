package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestNineDigitCodes(t *testing.T) {
	c := NewCodes("tok-tok-tok-tok-tok-tok")
	c.SetDigits(9)
	code := c.Show(false)
	if len(code) != 9 || strings.Trim(code, "0123456789") != "" {
		t.Fatalf("code %q", code)
	}
	if got := SpokenCode("482913557"); got != "4 8 2, 9 1 3, 5 5 7" {
		t.Fatalf("spoken %q", got)
	}
	if got := SpokenCode("123456"); got != "1 2 3 4 5 6" {
		t.Fatalf("spoken %q", got)
	}
	// The phone app asks how many digits to wait for.
	w := httptest.NewRecorder()
	c.Handler()(w, httptest.NewRequest(http.MethodGet, "/api/pair-code", nil))
	var body struct{ Digits int }
	if err := json.NewDecoder(w.Body).Decode(&body); err != nil || body.Digits != 9 {
		t.Fatalf("digits %d %v", body.Digits, err)
	}
	// Typed with spaces, as spoken.
	if ok, _ := c.Check(code[:3] + " " + code[3:6] + " " + code[6:]); !ok {
		t.Fatal("grouped code refused")
	}
}
