import type { Event, GameState } from './gen/simsight/v1/game_pb.js';
import { client, isUnauthenticated, token, useGame } from './client.js';
import { findGame, games, hosted, type Game } from './meeting.js';

/** waiting: at a meeting point, no paired Mac has its game running. */
export type Status = 'unpaired' | 'rejected' | 'connecting' | 'live' | 'retrying' | 'waiting';

/** How many events the feed keeps on the phone. */
const FEED_MAX = 100;

/**
 * Live game state from the bridge stream: the current state first, then
 * every change and event. Reconnects with backoff and pauses while the page
 * is hidden (iOS cuts streams in the background anyway); a reconnect starts
 * again from a fresh state.
 */
class Live {
	state = $state<GameState | null>(null);
	gameConnected = $state(false);
	/** Reached the computer at least once since the app opened. */
	reached = $state(false);
	status = $state<Status>((hosted ? games().length > 0 : !!token) ? 'connecting' : 'unpaired');
	/** At a meeting point: the Mac in use. */
	game = $state<Game | null>(null);
	/** Newest first. */
	feed = $state<Event[]>([]);
	/** Called for each event that arrives after the initial backlog. */
	onEvent: ((e: Event) => void) | null = null;
	#abort: AbortController | null = null;
	#delay = 1000;
	#lastSeq = 0n;

	start() {
		if (hosted ? games().length === 0 : !token) return;
		document.addEventListener('visibilitychange', () => (document.hidden ? this.stop() : this.#run()));
		this.#run();
	}

	stop() {
		this.#abort?.abort();
		this.#abort = null;
	}

	#add(e: Event, fresh: boolean) {
		if (e.seq <= this.#lastSeq) return; // already seen before a reconnect
		this.#lastSeq = e.seq;
		this.feed = [e, ...this.feed].slice(0, FEED_MAX);
		if (fresh) this.onEvent?.(e);
	}

	async #run() {
		if (this.#abort) return;
		const abort = new AbortController();
		this.#abort = abort;
		while (!abort.signal.aborted) {
			// At a meeting point: where is a paired Mac with its game running?
			// (Its address changes each session.)
			if (hosted) {
				const found = await findGame();
				if (abort.signal.aborted) return;
				if (!found) {
					this.status = 'waiting';
					await new Promise((r) => setTimeout(r, 8000));
					continue;
				}
				useGame(found.url, found.game.token);
				this.game = found.game;
			}
			// The bridge replays recent events right after connecting, in one
			// burst: those are shown in the feed but not announced.
			const backlogUntil = Date.now() + 1000;
			try {
				for await (const res of client.watch({}, { signal: abort.signal })) {
					this.status = 'live';
					this.reached = true;
					this.#delay = 1000;
					const p = res.payload;
					if (p.case === 'state') {
						this.state = p.value;
					} else if (p.case === 'event') {
						this.#add(p.value, Date.now() > backlogUntil);
					} else if (p.case === 'gameConnected') {
						this.gameConnected = p.value;
					}
				}
			} catch (err) {
				if (abort.signal.aborted) return;
				if (isUnauthenticated(err)) {
					this.status = 'rejected';
					this.stop();
					return;
				}
			}
			this.status = 'retrying';
			await new Promise((r) => setTimeout(r, this.#delay));
			this.#delay = Math.min(this.#delay * 2, 15000);
		}
	}
}

export const live = new Live();

/** The computer the game runs on, as the texts name it: "Mac" or "PC". */
export function computer(): { computer: string } {
	return { computer: live.state?.platform === 'windows' ? 'PC' : 'Mac' };
}
