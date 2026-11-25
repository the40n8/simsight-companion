// The app at a meeting point (companion/meeting-point): one fixed address
// for the phone, which finds each paired Mac wherever its tunnel is today.
// It asks this meeting point and any others the player added (Settings), so
// it keeps working if one stops. Served by the Mac itself instead, none of
// this is used.
import { refreshManifest } from './session.js';

/** True when this app is served by a meeting point (it marks the page). */
export const hosted =
	typeof document !== 'undefined' && !!document.querySelector('meta[name="simsight-meeting-point"]');

export type Game = { id: string; token: string; name: string };
export type Found = { game: Game; url: string };

const GAMES = 'simsight.games';
const POINTS = 'simsight.points';
const KEY = /^[A-Za-z0-9_-]{20,64}$/;

export function games(): Game[] {
	try {
		const list = JSON.parse(localStorage.getItem(GAMES) ?? '[]') as Game[];
		return Array.isArray(list) ? list.filter((g) => KEY.test(g.id) && KEY.test(g.token)) : [];
	} catch {
		return [];
	}
}

function store(list: Game[]): void {
	try {
		localStorage.setItem(GAMES, JSON.stringify(list));
	} catch {
		// private browsing: paired for this page only
	}
	// For "Add to Home Screen" (its own storage on iOS): the meeting point
	// puts these in the install's start address (see its manifest).
	try {
		const value = list.map((g) => `${g.id}.${g.token}`).join('~');
		const secure = location.protocol === 'https:' ? '; Secure' : '';
		document.cookie = value
			? `ssg=${value}; Path=/; Max-Age=31536000; SameSite=Strict${secure}`
			: 'ssg=; Path=/; Max-Age=0';
		refreshManifest();
	} catch {
		// cookies off
	}
}

export function saveGame(game: Game): void {
	const list = games().filter((g) => g.id !== game.id);
	const old = games().find((g) => g.id === game.id);
	store([{ ...game, name: game.name || old?.name || '' }, ...list]);
}

export function forgetGame(id: string): void {
	store(games().filter((g) => g.id !== id));
}

/** Takes the Mac and key from a link (#m=…&k=…, or #g=id.key~… from the Home Screen install). */
export function takeLink(text: string): boolean {
	const m = /[#&?]m=([A-Za-z0-9_-]{20,64})&k=([A-Za-z0-9_-]{20,64})/.exec(text);
	if (m) {
		saveGame({ id: m[1], token: m[2], name: '' });
		return true;
	}
	const g = /[#&]g=([A-Za-z0-9_.~-]+)/.exec(text);
	if (!g) return false;
	let any = false;
	for (const part of g[1].split('~')) {
		const [id, token] = part.split('.');
		if (KEY.test(id ?? '') && KEY.test(token ?? '')) {
			if (!games().some((x) => x.id === id)) saveGame({ id, token, name: '' });
			any = true;
		}
	}
	return any;
}

/** True while a pairing link is still in the address bar. */
export function linkInAddressBar(): boolean {
	return /(?:^#|&)(m|g)=/.test(location.hash);
}

type Where = { id: string; url: string; online: boolean; seen: number };

/** Meeting points to ask: this one first, then those added in Settings. */
export function points(): string[] {
	const own = typeof location !== 'undefined' ? location.origin : '';
	return [own, ...extraPoints().filter((p) => p !== own)];
}

/** Meeting points added in Settings (their addresses, https). */
export function extraPoints(): string[] {
	try {
		const list = JSON.parse(localStorage.getItem(POINTS) ?? '[]') as string[];
		return Array.isArray(list) ? list.filter(webOrigin) : [];
	} catch {
		return [];
	}
}

// https, or http on this device only (a meeting point being tried out).
function webOrigin(origin: string): boolean {
	return /^https:\/\/[^/\s]+\.[^/\s]+$/.test(origin) || /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin);
}

/** Adds a meeting point; false when the text is not a web address. */
export function addPoint(text: string): boolean {
	let url: URL;
	try {
		url = new URL(/^https?:\/\//i.test(text.trim()) ? text.trim() : `https://${text.trim()}`);
	} catch {
		return false;
	}
	if (!webOrigin(url.origin)) return false;
	const list = extraPoints().filter((p) => p !== url.origin);
	try {
		localStorage.setItem(POINTS, JSON.stringify([...list, url.origin]));
	} catch {
		return false;
	}
	return true;
}

export function removePoint(origin: string): void {
	try {
		localStorage.setItem(POINTS, JSON.stringify(extraPoints().filter((p) => p !== origin)));
	} catch {
		// not kept
	}
}

/** null: that meeting point did not answer. */
async function ask(point: string, path: string, init?: RequestInit): Promise<Response | null> {
	try {
		return await fetch(point + path, { cache: 'no-store', ...init });
	} catch {
		return null;
	}
}

/** Where a Mac is, from the first meeting point that knows it online. */
async function where(id: string): Promise<{ w: Where | null; reached: boolean }> {
	let reached = false;
	let best: Where | null = null;
	for (const res of await Promise.all(points().map((p) => ask(p, `/mp/where?id=${encodeURIComponent(id)}`)))) {
		if (!res) continue;
		reached = true;
		if (!res.ok) continue;
		try {
			const w = (await res.json()) as Where;
			if (w.online && w.url && (!best || w.seen > best.seen)) best = w;
		} catch {
			// a broken answer: ignored
		}
	}
	return { w: best, reached };
}

/**
 * The paired Mac whose game is running (the most recently seen when
 * several are); 'unreachable' when no meeting point answered at all.
 */
export async function findGame(): Promise<Found | null | 'unreachable'> {
	const list = games();
	const answers = await Promise.all(list.map((g) => where(g.id)));
	if (list.length > 0 && !answers.some((a) => a.reached)) return 'unreachable';
	let best: { game: Game; w: Where } | null = null;
	answers.forEach(({ w }, i) => {
		if (w && (!best || w.seen > best.w.seen)) best = { game: list[i], w };
	});
	const found = best as { game: Game; w: Where } | null;
	return found ? { game: found.game, url: found.w.url } : null;
}

/** The computer's own name, learned from it once connected. */
export function nameGame(id: string, name: string): void {
	const game = games().find((g) => g.id === id);
	if (game && name && game.name !== name) saveGame({ ...game, name });
}

/** Pairs with the code the Mac says: a meeting point knows which Mac shows it. */
export async function pairWithCode(code: string): Promise<'ok' | 'wrong' | 'locked' | 'offline'> {
	let mac: Where | null = null;
	let answered = false;
	for (const point of points()) {
		const res = await ask(point, '/mp/code', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ code })
		});
		if (!res) continue;
		answered = true;
		if (res.status === 429) return 'locked';
		if (!res.ok) continue;
		mac = (await res.json()) as Where;
		break;
	}
	if (!mac) return answered ? 'wrong' : 'offline';
	if (!mac.url) return 'offline';
	try {
		const pair = await fetch(`${mac.url}/api/pair-code`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ code })
		});
		if (pair.status === 429) return 'locked';
		if (!pair.ok) return 'wrong';
		const { token } = (await pair.json()) as { token?: string };
		if (!token || !KEY.test(token)) return 'wrong';
		saveGame({ id: mac.id, token, name: '' });
		return 'ok';
	} catch {
		return 'offline';
	}
}

// A pairing link opened here (from the Mac's QR code, or the Home Screen install).
if (hosted) takeLink(location.hash);
