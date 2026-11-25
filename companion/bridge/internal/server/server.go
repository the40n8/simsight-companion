// Package server exposes the companion API (Connect) and the web app to
// phones on the local network.
package server

import (
	"context"
	"crypto/subtle"
	"errors"
	"io/fs"
	"net/http"
	"slices"
	"strings"
	"time"

	"connectrpc.com/connect"
	v1 "github.com/the40n8/simsight-companion/companion/bridge/gen/simsight/v1"
	"github.com/the40n8/simsight-companion/companion/bridge/gen/simsight/v1/simsightv1connect"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/hub"
)

type service struct {
	hub *hub.Hub
}

var _ simsightv1connect.CompanionServiceHandler = (*service)(nil)

// heartbeat keeps quiet streams alive through proxies and tunnels that cut
// idle connections (Cloudflare: about 100 s), e.g. while the game is paused.
const heartbeat = 30 * time.Second

func (s *service) Watch(ctx context.Context, _ *connect.Request[v1.WatchRequest], stream *connect.ServerStream[v1.WatchResponse]) error {
	msgs := s.hub.Subscribe(ctx)
	tick := time.NewTicker(heartbeat)
	defer tick.Stop()
	for {
		select {
		case msg, ok := <-msgs:
			if !ok {
				return nil
			}
			if err := stream.Send(msg); err != nil {
				return err
			}
		case <-tick.C:
			// Repeats the connection flag: harmless for the app, keeps bytes flowing.
			if err := stream.Send(&v1.WatchResponse{Payload: &v1.WatchResponse_GameConnected{GameConnected: s.hub.Connected()}}); err != nil {
				return err
			}
		}
	}
}

func (s *service) SetSpeed(ctx context.Context, req *connect.Request[v1.SetSpeedRequest]) (*connect.Response[v1.SetSpeedResponse], error) {
	switch req.Msg.GetSpeed() {
	case v1.Speed_SPEED_PAUSED, v1.Speed_SPEED_NORMAL, v1.Speed_SPEED_FAST, v1.Speed_SPEED_ULTRA:
	default:
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("speed must be paused, normal, fast or ultra"))
	}
	out := s.hub.Command(ctx, &v1.ToGame{Command: &v1.ToGame_SetSpeed{SetSpeed: req.Msg}})
	return connect.NewResponse(&v1.SetSpeedResponse{Outcome: out}), nil
}

func (s *service) SelectSim(ctx context.Context, req *connect.Request[v1.SelectSimRequest]) (*connect.Response[v1.SelectSimResponse], error) {
	out := s.hub.Command(ctx, &v1.ToGame{Command: &v1.ToGame_SelectSim{SelectSim: req.Msg}})
	return connect.NewResponse(&v1.SelectSimResponse{Outcome: out}), nil
}

func (s *service) CancelAction(ctx context.Context, req *connect.Request[v1.CancelActionRequest]) (*connect.Response[v1.CancelActionResponse], error) {
	out := s.hub.Command(ctx, &v1.ToGame{Command: &v1.ToGame_CancelAction{CancelAction: req.Msg}})
	return connect.NewResponse(&v1.CancelActionResponse{Outcome: out}), nil
}

func (s *service) SetNarration(ctx context.Context, req *connect.Request[v1.SetNarrationRequest]) (*connect.Response[v1.SetNarrationResponse], error) {
	if req.Msg.GetNarration() == nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("narration is required"))
	}
	out := s.hub.Command(ctx, &v1.ToGame{Command: &v1.ToGame_SetNarration{SetNarration: req.Msg}})
	return connect.NewResponse(&v1.SetNarrationResponse{Outcome: out}), nil
}

func (s *service) ListTargets(ctx context.Context, req *connect.Request[v1.ListTargetsRequest]) (*connect.Response[v1.ListTargetsResponse], error) {
	res := s.hub.Call(ctx, &v1.ToGame{Command: &v1.ToGame_ListTargets{ListTargets: req.Msg}})
	return connect.NewResponse(&v1.ListTargetsResponse{Outcome: res.GetOutcome(), Targets: res.GetTargets()}), nil
}

func (s *service) GetInteractions(ctx context.Context, req *connect.Request[v1.GetInteractionsRequest]) (*connect.Response[v1.GetInteractionsResponse], error) {
	if req.Msg.GetTargetId() == "" {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("target_id is required"))
	}
	res := s.hub.Call(ctx, &v1.ToGame{Command: &v1.ToGame_GetInteractions{GetInteractions: req.Msg}})
	return connect.NewResponse(&v1.GetInteractionsResponse{Outcome: res.GetOutcome(),
		Interactions: res.GetInteractions(), NamesPending: res.GetNamesPending()}), nil
}

func (s *service) RunInteraction(ctx context.Context, req *connect.Request[v1.RunInteractionRequest]) (*connect.Response[v1.RunInteractionResponse], error) {
	if req.Msg.GetTargetId() == "" || req.Msg.GetInteractionId() == "" {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("target_id and interaction_id are required"))
	}
	out := s.hub.Command(ctx, &v1.ToGame{Command: &v1.ToGame_RunInteraction{RunInteraction: req.Msg}})
	return connect.NewResponse(&v1.RunInteractionResponse{Outcome: out}), nil
}

func (s *service) GetHouseMap(ctx context.Context, req *connect.Request[v1.GetHouseMapRequest]) (*connect.Response[v1.GetHouseMapResponse], error) {
	res := s.hub.Call(ctx, &v1.ToGame{Command: &v1.ToGame_GetHouseMap{GetHouseMap: req.Msg}})
	return connect.NewResponse(&v1.GetHouseMapResponse{Outcome: res.GetOutcome(), Map: res.GetHouseMap()}), nil
}

func (s *service) RespondDialog(ctx context.Context, req *connect.Request[v1.RespondDialogRequest]) (*connect.Response[v1.RespondDialogResponse], error) {
	if req.Msg.GetDialogId() == "" {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("dialog_id is required"))
	}
	out := s.hub.Command(ctx, &v1.ToGame{Command: &v1.ToGame_RespondDialog{RespondDialog: req.Msg}})
	return connect.NewResponse(&v1.RespondDialogResponse{Outcome: out}), nil
}

func (s *service) SetCamera(ctx context.Context, req *connect.Request[v1.SetCameraRequest]) (*connect.Response[v1.SetCameraResponse], error) {
	out := s.hub.Command(ctx, &v1.ToGame{Command: &v1.ToGame_SetCamera{SetCamera: req.Msg}})
	return connect.NewResponse(&v1.SetCameraResponse{Outcome: out}), nil
}

func (s *service) SetMacAnnounce(ctx context.Context, req *connect.Request[v1.SetMacAnnounceRequest]) (*connect.Response[v1.SetMacAnnounceResponse], error) {
	if err := s.hub.SetMacAnnounce(req.Msg.GetMacAnnounce()); err != nil {
		return connect.NewResponse(&v1.SetMacAnnounceResponse{Outcome: &v1.CommandOutcome{Reason: "error"}}), nil
	}
	return connect.NewResponse(&v1.SetMacAnnounceResponse{Outcome: &v1.CommandOutcome{Ok: true}}), nil
}

func (s *service) ListInventory(ctx context.Context, req *connect.Request[v1.ListInventoryRequest]) (*connect.Response[v1.ListInventoryResponse], error) {
	res := s.hub.Call(ctx, &v1.ToGame{Command: &v1.ToGame_ListInventory{ListInventory: req.Msg}})
	return connect.NewResponse(&v1.ListInventoryResponse{Outcome: res.GetOutcome(), Items: res.GetInventory()}), nil
}

func (s *service) MoveItem(ctx context.Context, req *connect.Request[v1.MoveItemRequest]) (*connect.Response[v1.MoveItemResponse], error) {
	if req.Msg.GetItemId() == "" || req.Msg.GetDestinationId() == "" {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("item_id and destination_id are required"))
	}
	out := s.hub.Command(ctx, &v1.ToGame{Command: &v1.ToGame_MoveItem{MoveItem: req.Msg}})
	return connect.NewResponse(&v1.MoveItemResponse{Outcome: out}), nil
}

// authInterceptor accepts only requests carrying the pairing token.
func authInterceptor(token string) connect.Interceptor {
	check := func(h http.Header) error {
		got := strings.TrimPrefix(h.Get("Authorization"), "Bearer ")
		if subtle.ConstantTimeCompare([]byte(got), []byte(token)) != 1 {
			return connect.NewError(connect.CodeUnauthenticated, errors.New("pair this phone again with the link from the Mac"))
		}
		return nil
	}
	return &auth{check: check}
}

type auth struct{ check func(http.Header) error }

func (a *auth) WrapUnary(next connect.UnaryFunc) connect.UnaryFunc {
	return func(ctx context.Context, req connect.AnyRequest) (connect.AnyResponse, error) {
		if err := a.check(req.Header()); err != nil {
			return nil, err
		}
		return next(ctx, req)
	}
}

func (a *auth) WrapStreamingClient(next connect.StreamingClientFunc) connect.StreamingClientFunc {
	return next
}

func (a *auth) WrapStreamingHandler(next connect.StreamingHandlerFunc) connect.StreamingHandlerFunc {
	return func(ctx context.Context, conn connect.StreamingHandlerConn) error {
		if err := a.check(conn.RequestHeader()); err != nil {
			return err
		}
		return next(ctx, conn)
	}
}

// Handler serves the API, the web app (an SPA: unknown paths get
// index.html) and, on this Mac only, the pairing page at /pair.
// cors are the meeting points' origins, allowed to call this API from the
// phone app they host (nil: none). setup is the settings page
// (/setup, this Mac only; nil for none).
func Handler(h *hub.Hub, token string, web fs.FS, links LinkFunc, codes *Codes, cors []string, setup http.Handler) http.Handler {
	mux := http.NewServeMux()
	mux.Handle("/pair", PairPage(links, codes))
	if setup != nil {
		mux.Handle("/setup", setup)
	}
	if codes != nil {
		mux.Handle("/api/pair-code", codes.Handler())
	}
	mux.Handle("/manifest.json", Manifest(web, token))
	path, api := simsightv1connect.NewCompanionServiceHandler(&service{hub: h},
		connect.WithInterceptors(authInterceptor(token)))
	mux.Handle(path, api)

	files := http.FileServerFS(web)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Referrer-Policy", "no-referrer")
		name := strings.TrimPrefix(r.URL.Path, "/")
		if name != "" {
			if _, err := fs.Stat(web, name); err == nil {
				if strings.HasPrefix(name, "_app/immutable/") {
					w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
				}
				files.ServeHTTP(w, r)
				return
			}
		}
		w.Header().Set("Cache-Control", "no-cache")
		http.ServeFileFS(w, r, web, "index.html")
	})
	if len(cors) == 0 {
		return mux
	}
	return allowOrigin(cors, mux)
}

// allowOrigin lets the phone app at the meeting point use this API across
// origins (it still needs the pairing key for everything but the code).
func allowOrigin(origins []string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if origin == "" || !slices.Contains(origins, origin) {
			next.ServeHTTP(w, r)
			return
		}
		h := w.Header()
		h.Set("Access-Control-Allow-Origin", origin)
		h.Add("Vary", "Origin")
		h.Set("Access-Control-Expose-Headers", "Grpc-Status, Grpc-Message, Grpc-Status-Details-Bin, Connect-Content-Encoding")
		if r.Method == http.MethodOptions {
			h.Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			h.Set("Access-Control-Allow-Headers", "Authorization, Content-Type, Connect-Protocol-Version, Connect-Timeout-Ms, Connect-Accept-Encoding, Connect-Content-Encoding, X-User-Agent, X-Grpc-Web, Grpc-Timeout")
			h.Set("Access-Control-Max-Age", "7200")
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}
