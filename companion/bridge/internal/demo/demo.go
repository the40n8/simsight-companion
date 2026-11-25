// Package demo is a fake game for developing the phone app without The
// Sims 4: a two-Sim household whose needs drift, with commands that work.
package demo

import (
	"context"
	"fmt"
	"math/rand/v2"
	"time"

	v1 "github.com/the40n8/simsight-companion/companion/bridge/gen/simsight/v1"
	"github.com/the40n8/simsight-companion/companion/bridge/internal/hub"
	"google.golang.org/protobuf/proto"
)

type game struct {
	hub     *hub.Hub
	cmds    chan *v1.ToGame
	state   *v1.GameState
	minutes int
	nextID  int
	// The controlled Sim's inventory (two fridges accept food).
	inventory []*v1.InventoryItem
}

var activities = []string{"Cook Dinner", "Watch TV", "Chat", "Read a Book", "Take Shower", "Play Video Games", "Nap"}

// Run drives the fake household until ctx ends.
func Run(ctx context.Context, h *hub.Hub) {
	g := &game{hub: h, cmds: make(chan *v1.ToGame, 16), minutes: 8 * 60}
	fridges := []*v1.Target{{Id: "o1", Name: "Fridge", Group: "APPLIANCES", Kind: "Refrigerators", Distance: 3},
		{Id: "o12", Name: "Mini Fridge", Group: "APPLIANCES", Kind: "Refrigerators", Distance: 9}}
	g.inventory = []*v1.InventoryItem{
		{Item: &v1.Target{Id: "i1", Name: "Mac and Cheese", Group: "MISC", Kind: "Food"}, Count: 2, Destinations: fridges},
		{Item: &v1.Target{Id: "i2", Name: "Mystery Novel", Group: "MISC", Kind: "Books"}, Count: 1,
			Destinations: []*v1.Target{{Id: "o13", Name: "Bookcase", Group: "STORAGE", Kind: "Bookshelves", Distance: 6}}},
	}
	g.state = &v1.GameState{
		InGame:        true,
		Funds:         20450,
		HouseholdName: "Goth",
		Clock:         &v1.Clock{Speed: v1.Speed_SPEED_NORMAL},
		Narration:     &v1.Narration{MacVoice: true, SimActions: true},
		BillsOwed:     340,
		Visitors:      []*v1.Visitor{{SimId: "2001", Name: "Nancy Landgraab", Room: &v1.RoomRef{Id: "outside:0", Kind: "OUTSIDE"}}},
		Problems: []*v1.Problem{{Kind: "FOOD", TargetId: "o11", Name: "Mac and Cheese", Distance: 2},
			{Kind: "BROKEN", TargetId: "o3", Name: "Shower", Distance: 8},
			{Kind: "PUDDLE", TargetId: "o10", Name: "Puddle", Distance: 7}},
		Sims: []*v1.Sim{
			g.sim("1001", "Bella", "Goth", true),
			g.sim("1002", "Mortimer", "Goth", false),
		},
	}
	h.SetSender(func(c *v1.ToGame) error { g.cmds <- c; return nil })
	h.SetConnected(true)
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	g.publish()
	for {
		select {
		case <-ctx.Done():
			return
		case c := <-g.cmds:
			res := g.call(c)
			res.Id = c.GetId()
			h.Resolve(res)
			g.publish()
		case <-tick.C:
			g.step()
			g.publish()
		}
	}
}

func (g *game) sim(id, first, last string, active bool) *v1.Sim {
	s := &v1.Sim{Id: id, FirstName: first, LastName: last, Active: active, Here: true,
		Mood: &v1.Mood{Key: "happy", Name: "Happy", Intensity: 1}}
	for _, k := range []string{"hunger", "bladder", "energy", "hygiene", "fun", "social"} {
		s.Needs = append(s.Needs, &v1.Need{Key: k, Percent: int32(50 + rand.IntN(50))})
	}
	s.Moodlets = []*v1.Moodlet{{Name: "Well Rested", Description: "Slept like a log.", MoodKey: "energized", Weight: 1, MinutesLeft: 240}}
	g.addAction(s, activities[rand.IntN(len(activities))])
	s.Room = &v1.RoomRef{Id: "r1", Kind: "KITCHEN"}
	if !active {
		s.Room = &v1.RoomRef{Id: "r2", Kind: "LIVING"}
	}
	s.Skills = []*v1.Skill{{Key: "cooking", Name: "Cooking", Level: 4, MaxLevel: 10}, {Key: "charisma", Name: "Charisma", Level: 2, MaxLevel: 10}}
	s.Relationships = []*v1.Relationship{{SimId: "2001", Name: "Nancy Landgraab", Friendship: 35, Labels: []string{"Friend"}},
		{SimId: "2002", Name: "Eliza Pancakes", Friendship: -20, Labels: []string{"Disliked"}}}
	s.Goals = &v1.Goals{Aspiration: "Master Chef", Milestone: "Kitchen Novice",
		MilestoneSteps: []*v1.Objective{{Name: "Reach Cooking skill 3", Done: true}, {Name: "Cook 5 meals", Done: false}},
		Wants:          []*v1.Want{{Name: "Chat with Nancy"}, {Name: "Fear of being alone", Fear: true}}}
	if active {
		s.Goals.WorkTasks = []*v1.Objective{{Name: "Practice cooking at home"}}
		s.Goals.PromotionSteps = []*v1.Objective{{Name: "Reach Cooking skill 4", Done: false}}
		perf := int32(62)
		s.Career, s.Performance = "Line Cook", &perf
	}
	return s
}

func (g *game) addAction(s *v1.Sim, name string) {
	g.nextID++
	state := v1.ActionState_ACTION_STATE_RUNNING
	if len(s.Actions) > 0 {
		state = v1.ActionState_ACTION_STATE_QUEUED
	}
	s.Actions = append(s.Actions, &v1.Action{Id: fmt.Sprint(g.nextID), Name: name, State: state, Cancelable: true})
}

func level(p int32) v1.NeedLevel {
	switch {
	case p < 15:
		return v1.NeedLevel_NEED_LEVEL_CRITICAL
	case p < 30:
		return v1.NeedLevel_NEED_LEVEL_LOW
	case p < 60:
		return v1.NeedLevel_NEED_LEVEL_OKAY
	default:
		return v1.NeedLevel_NEED_LEVEL_GOOD
	}
}

func (g *game) step() {
	st := g.state
	if st.Clock.Speed == v1.Speed_SPEED_PAUSED {
		return
	}
	g.minutes += map[v1.Speed]int{v1.Speed_SPEED_NORMAL: 1, v1.Speed_SPEED_FAST: 3, v1.Speed_SPEED_ULTRA: 8}[st.Clock.Speed]
	for _, s := range st.Sims {
		for _, n := range s.Needs {
			n.Percent = max(0, min(100, n.Percent+int32(rand.IntN(5))-3))
			n.Level = level(n.Percent)
		}
		if rand.IntN(12) == 0 {
			name := activities[rand.IntN(len(activities))]
			if len(s.Actions) > 0 {
				s.Actions = s.Actions[1:]
				if len(s.Actions) > 0 {
					s.Actions[0].State = v1.ActionState_ACTION_STATE_RUNNING
				}
			}
			g.addAction(s, name)
		}
	}
	if rand.IntN(15) == 0 {
		// A chat with Nancy: friendship moves, sometimes a new label.
		s := st.Sims[0]
		g.addAction(s, "Tell Joke")
		r := s.Relationships[0]
		r.Friendship = min(100, r.Friendship+int32(2+rand.IntN(4)))
		if r.Friendship >= 50 && len(r.Labels) < 2 {
			r.Labels = append(r.Labels, "Good Friends")
		}
	}
	if rand.IntN(120) == 0 {
		g.hub.AddEvent(&v1.Event{Kind: v1.EventKind_EVENT_KIND_ROUTE_FAIL, SimId: st.Sims[0].Id, SimName: st.Sims[0].FirstName,
			Subject: "Take Shower", Detail: "NO_PATH_FOUND"})
	}
	if rand.IntN(90) == 0 {
		g.hub.AddEvent(&v1.Event{Kind: v1.EventKind_EVENT_KIND_NOTIFICATION, Subject: "Bills Due", Detail: "The household bills of §340 are due."})
	}
	if rand.IntN(20) == 0 {
		s := st.Sims[rand.IntN(len(st.Sims))]
		moods := []struct{ key, name string }{{"happy", "Happy"}, {"energized", "Energized"}, {"playful", "Playful"}, {"bored", "Bored"}, {"tense", "Tense"}}
		m := moods[rand.IntN(len(moods))]
		s.Mood = &v1.Mood{Key: m.key, Name: m.name, Intensity: 1}
	}
}

func (g *game) publish() {
	m := g.minutes % (24 * 60)
	days := []string{"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
	g.state.Clock.Day = days[(g.minutes/(24*60))%7]
	g.state.Clock.Time = fmt.Sprintf("%02d:%02d", m/60, m%60)
	g.state.Seq++
	g.hub.SetState(proto.Clone(g.state).(*v1.GameState))
}

func (g *game) find(id string) *v1.Sim {
	for _, s := range g.state.Sims {
		if s.Id == id {
			return s
		}
	}
	return nil
}

const (
	okID     = 10001
	cancelID = 10002
)

func (g *game) active() *v1.Sim {
	for _, s := range g.state.Sims {
		if s.Active {
			return s
		}
	}
	return g.state.Sims[0]
}

var demoTargets = []*v1.Target{
	{Id: "o1", Name: "Fridge", Group: "APPLIANCES", Distance: 3},
	{Id: "o2", Name: "Sofa", Group: "COMFORT", Distance: 5},
	{Id: "o3", Name: "Shower", Group: "PLUMBING", Distance: 8},
	{Id: "o4", Name: "Computer", Group: "ELECTRONICS", Distance: 9},
	{Id: "o9", Name: "Mailbox", Group: "MISC", Kind: "Mailboxes", Keywords: "Mailbox", Distance: 12},
}

var demoMenus = map[string][]*v1.Interaction{
	"self": {{Id: "1", Name: "Check Mood", Enabled: true}, {Id: "2", Name: "Call Someone", Category: "Phone", Enabled: true}},
	"sim":  {{Id: "1", Name: "Chat", Category: "Friendly", Enabled: true}, {Id: "2", Name: "Tell Joke", Category: "Funny", Enabled: true}, {Id: "3", Name: "Ask to Be Boyfriend", Category: "Romance", Reason: "Relationship too low"}},
	"o1":   {{Id: "1", Name: "Quick Meal", Category: "Grab", Enabled: true}, {Id: "2", Name: "Cook", Enabled: true}},
	"o2":   {{Id: "1", Name: "Sit", Enabled: true}, {Id: "2", Name: "Nap", Enabled: true}},
	"o3":   {{Id: "1", Name: "Take Shower", Reason: "Broken", Enabled: false}, {Id: "2", Name: "Repair", Enabled: true}},
	"o10":  {{Id: "1", Name: "Mop Up", Enabled: true}},
	"o11":  {{Id: "1", Name: "Grab a Plate", Enabled: true}, {Id: "2", Name: "Put Away", Enabled: true}},
	"o9":   {{Id: "1", Name: "Pay Bills", Enabled: true}, {Id: "2", Name: "Check Mail", Enabled: true}},
	"2001": {{Id: "1", Name: "Greet", Enabled: true}, {Id: "2", Name: "Welcome", Category: "Friendly", Enabled: true}},
	"o4":   {{Id: "1", Name: "Play Games", Enabled: true}, {Id: "2", Name: "Browse Web", Enabled: true}, {Id: "3", Name: "Write a Book", Enabled: true}},
	"phone": {{Id: "1", Name: "Call to Chat...", Category: "Social", Enabled: true}, {Id: "2", Name: "Invite Over...", Category: "Social", Enabled: true},
		{Id: "3", Name: "Order Pizza", Category: "Household", Enabled: true},
		{Id: "5", Name: "Hire a Maid", Category: "Household", Enabled: true}, {Id: "6", Name: "Hire a Repair Technician", Category: "Household", Enabled: true}, {Id: "4", Name: "Travel", Category: "Travel", Enabled: true}},
}

func item(t *v1.Target, side string) *v1.RoomItem { return &v1.RoomItem{Target: t, Side: side} }

// demoHouse: a small two-floor house around the demo objects.
func demoHouse() *v1.HouseMap {
	return &v1.HouseMap{Floors: []int32{0, 1}, Stairs: []*v1.Stairs{{Floor: 0, Part: "E", BottomRoomId: "r2", TopRoomId: "r4"}}, Rooms: []*v1.Room{
		{Id: "r1", Kind: "KITCHEN", Floor: 0, Width: 4, Depth: 5, Area: 20, Part: "NW", Here: true, Sims: []string{"Bella"},
			Links: []*v1.RoomLink{{RoomId: "r2", Door: false, Side: "E"}, {RoomId: "outside:0", Door: true, Side: "N"}},
			Items: []*v1.RoomItem{item(demoTargets[0], "N"), item(&v1.Target{Id: "o5", Name: "Counter", Group: "SURFACE", Kind: "Counters"}, "W")}},
		{Id: "r2", Kind: "LIVING", Kind2: "DINING", Areas: []*v1.RoomArea{{Kind: "LIVING", Side: "N"}, {Kind: "DINING", Side: "S"}}, Floor: 0, Width: 6, Depth: 5, Area: 30, Part: "NE", Distance: 5, Bearing: "E",
			Links: []*v1.RoomLink{{RoomId: "r4", Stairs: "up", Side: "E"}, {RoomId: "r1", Side: "W"}, {RoomId: "r3", Door: true, Side: "S"}, {RoomId: "outside:0", Door: true, Side: "E"}},
			Items: []*v1.RoomItem{item(demoTargets[1], "C"), item(&v1.Target{Id: "o6", Name: "TV", Group: "ELECTRONICS", Kind: "Televisions"}, "N")}},
		{Id: "r3", Kind: "BATHROOM", Floor: 0, Width: 2, Depth: 3, Area: 6, Part: "S", Distance: 8, Bearing: "SE",
			Links: []*v1.RoomLink{{RoomId: "r2", Door: true, Side: "N"}},
			Items: []*v1.RoomItem{item(demoTargets[2], "E")}},
		{Id: "r4", Kind: "BEDROOM", Number: 0, Floor: 1, Width: 4, Depth: 4, Area: 16, Part: "C", Distance: 9, Bearing: "N",
			Links: []*v1.RoomLink{{RoomId: "r2", Stairs: "down", Side: "E"}},
			Items: []*v1.RoomItem{item(demoTargets[3], "S"), item(&v1.Target{Id: "o7", Name: "Double Bed", Group: "COMFORT", Kind: "Beds"}, "N")}},
		{Id: "outside:0", Kind: "OUTSIDE", Floor: 0, Width: 20, Depth: 15, Area: 200, Part: "C", Distance: 6, Bearing: "S",
			Links: []*v1.RoomLink{{RoomId: "r2", Door: true, Side: "E"}, {RoomId: "r1", Door: true, Side: "N"}},
			Items: []*v1.RoomItem{item(&v1.Target{Id: "o8", Name: "Grill", Group: "APPLIANCES", Kind: "Grills"}, "S"), item(&v1.Target{Id: "o9", Name: "Mailbox", Group: "MISC", Kind: "Mailboxes"}, "E")}},
	}}
}

func (g *game) call(c *v1.ToGame) *v1.CommandResult {
	switch cmd := c.Command.(type) {
	case *v1.ToGame_ListInventory:
		return &v1.CommandResult{Outcome: &v1.CommandOutcome{Ok: true}, Inventory: g.inventory}
	case *v1.ToGame_MoveItem:
		for i, it := range g.inventory {
			if it.GetItem().GetId() == cmd.MoveItem.GetItemId() {
				g.inventory = append(g.inventory[:i], g.inventory[i+1:]...)
				return &v1.CommandResult{Outcome: &v1.CommandOutcome{Ok: true}}
			}
		}
		return &v1.CommandResult{Outcome: &v1.CommandOutcome{Reason: "target_unavailable"}}
	case *v1.ToGame_GetHouseMap:
		return &v1.CommandResult{Outcome: &v1.CommandOutcome{Ok: true}, HouseMap: demoHouse()}
	case *v1.ToGame_ListTargets:
		var targets []*v1.Target
		for _, s := range g.state.Sims {
			targets = append(targets, &v1.Target{Id: s.Id, Name: s.FirstName + " " + s.LastName, IsSim: true, IsSelf: s.Active, Group: "SIMS", Distance: 2})
			if s.Active {
				targets = append(targets, &v1.Target{Id: "phone", Name: "Phone", Group: "PHONE"})
			}
		}
		return &v1.CommandResult{Outcome: &v1.CommandOutcome{Ok: true}, Targets: append(targets, demoTargets...)}
	case *v1.ToGame_GetInteractions:
		key := cmd.GetInteractions.GetTargetId()
		if s := g.find(key); s != nil {
			key = "sim"
			if s.Active {
				key = "self"
			}
		}
		menu, ok := demoMenus[key]
		if !ok {
			return &v1.CommandResult{Outcome: &v1.CommandOutcome{Reason: "target_unavailable"}}
		}
		return &v1.CommandResult{Outcome: &v1.CommandOutcome{Ok: true}, Interactions: menu}
	case *v1.ToGame_RunInteraction:
		ri := cmd.RunInteraction
		if ri.GetTargetId() == "o9" && ri.GetInteractionId() == "1" {
			g.state.Funds -= g.state.BillsOwed
			g.state.BillsOwed = 0
		}
		if ri.GetTargetId() == "o1" && ri.GetInteractionId() == "2" {
			g.nextID++
			g.state.Dialogs = append(g.state.Dialogs, &v1.Dialog{
				Id: fmt.Sprint(g.nextID), Title: "Cook", Text: "What should Bella cook?",
				Choices: []*v1.DialogChoice{{Id: okID}, {Id: cancelID}}, MinSelectable: 1, MaxSelectable: 1,
				Rows: []*v1.DialogRow{
					{Id: "11", Name: "Mac and Cheese", Description: "§12, 4 servings", Enabled: true},
					{Id: "12", Name: "Pancakes", Description: "§9, 4 servings", Enabled: true},
					{Id: "13", Name: "Lobster Thermidor", Description: "§80", Reason: "Requires Cooking level 9"},
				},
			})
			return &v1.CommandResult{Outcome: &v1.CommandOutcome{Ok: true}}
		}
		// A game text box, like naming a baby or a book.
		if ri.GetTargetId() == "o4" && ri.GetInteractionId() == "3" {
			g.nextID++
			g.state.Dialogs = append(g.state.Dialogs, &v1.Dialog{
				Id: fmt.Sprint(g.nextID), Title: "Write a Book", Text: "Name your book.",
				Choices:   []*v1.DialogChoice{{Id: okID}, {Id: cancelID}},
				TextInput: "title", TextDefault: "My Book",
			})
			return &v1.CommandResult{Outcome: &v1.CommandOutcome{Ok: true}}
		}
		// Fixing a problem clears it.
		for i, p := range g.state.Problems {
			if p.GetTargetId() == ri.GetTargetId() {
				g.state.Problems = append(g.state.Problems[:i], g.state.Problems[i+1:]...)
				break
			}
		}
		g.addAction(g.active(), "Interaction "+ri.GetTargetId()+"/"+ri.GetInteractionId())
		return &v1.CommandResult{Outcome: &v1.CommandOutcome{Ok: true}}
	case *v1.ToGame_RespondDialog:
		rd := cmd.RespondDialog
		for i, d := range g.state.Dialogs {
			if d.GetId() != rd.GetDialogId() {
				continue
			}
			g.state.Dialogs = append(g.state.Dialogs[:i], g.state.Dialogs[i+1:]...)
			if rd.GetChoiceId() == okID && d.GetTextInput() != "" {
				g.addAction(g.active(), "Write "+rd.GetText())
			}
			if rd.GetChoiceId() == okID && len(rd.GetRowIds()) > 0 {
				for _, r := range d.GetRows() {
					if r.GetId() == rd.GetRowIds()[0] {
						g.addAction(g.active(), "Cook "+r.GetName())
					}
				}
			}
			return &v1.CommandResult{Outcome: &v1.CommandOutcome{Ok: true}}
		}
		return &v1.CommandResult{Outcome: &v1.CommandOutcome{Reason: "dialog_closed"}}
	}
	return &v1.CommandResult{Outcome: g.apply(c)}
}

func (g *game) apply(c *v1.ToGame) *v1.CommandOutcome {
	ok := &v1.CommandOutcome{Ok: true}
	switch cmd := c.Command.(type) {
	case *v1.ToGame_SetSpeed:
		g.state.Clock.Speed = cmd.SetSpeed.GetSpeed()
		return ok
	case *v1.ToGame_SelectSim:
		target := g.find(cmd.SelectSim.GetSimId())
		if target == nil {
			return &v1.CommandOutcome{Reason: "sim_unavailable"}
		}
		for _, s := range g.state.Sims {
			s.Active = s == target
		}
		return ok
	case *v1.ToGame_CancelAction:
		s := g.find(cmd.CancelAction.GetSimId())
		if s == nil {
			return &v1.CommandOutcome{Reason: "sim_unavailable"}
		}
		for i, a := range s.Actions {
			if a.Id == cmd.CancelAction.GetActionId() {
				s.Actions = append(s.Actions[:i], s.Actions[i+1:]...)
				if len(s.Actions) > 0 {
					s.Actions[0].State = v1.ActionState_ACTION_STATE_RUNNING
				}
				return ok
			}
		}
		return &v1.CommandOutcome{Reason: "action_finished"}
	case *v1.ToGame_SetCamera:
		if cmd.SetCamera.GetMode() == v1.CameraMode_CAMERA_MODE_FREE {
			g.state.CameraFollows = ""
		} else {
			g.state.CameraFollows = cmd.SetCamera.GetSimId()
		}
		return ok
	case *v1.ToGame_SetNarration:
		g.state.Narration = cmd.SetNarration.GetNarration()
		return ok
	}
	return &v1.CommandOutcome{Reason: "unknown_command"}
}
