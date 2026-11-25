/// <reference no-default-lib="true"/>
/// <reference lib="esnext" />
/// <reference lib="webworker" />
// The app opens even when the computer cannot be reached (game closed,
// tunnel down): a copy of the app stays on the phone, so instead of a
// Cloudflare or Safari error page the app says it is waiting for the game
// and connects by itself when it is back. Only the app's own files are
// kept; the game's live data always comes from the network.
import { build, files, version } from '$service-worker';

const sw = self as unknown as ServiceWorkerGlobalScope;
const CACHE = `simsight-${version}`;
const SHELL = '/';
// manifest.json is made for each phone (its pairing link): never kept.
const ASSETS = [...build, ...files.filter((f) => f !== '/manifest.json')];

sw.addEventListener('install', (event) => {
	event.waitUntil(
		(async () => {
			const cache = await caches.open(CACHE);
			await cache.addAll([...ASSETS, SHELL]);
			await sw.skipWaiting();
		})()
	);
});

sw.addEventListener('activate', (event) => {
	event.waitUntil(
		(async () => {
			for (const key of await caches.keys()) {
				if (key !== CACHE) await caches.delete(key);
			}
			await sw.clients.claim();
		})()
	);
});

sw.addEventListener('fetch', (event) => {
	const req = event.request;
	if (req.method !== 'GET') return;
	const url = new URL(req.url);
	if (url.origin !== sw.location.origin) return;

	// Opening the app: the computer's current version when it answers, the
	// kept copy when it does not (no network, or Cloudflare's 5xx page).
	if (req.mode === 'navigate') {
		if (url.pathname !== '/') return; // /pair, /setup: the computer's own pages
		event.respondWith(
			(async () => {
				const cache = await caches.open(CACHE);
				try {
					const res = await fetch(req);
					if (res.ok) {
						await cache.put(SHELL, res.clone());
						return res;
					}
					return (await cache.match(SHELL)) ?? res;
				} catch (err) {
					const kept = await cache.match(SHELL);
					if (kept) return kept;
					throw err;
				}
			})()
		);
		return;
	}
	// The app's files: built once per version, so the kept copy is right.
	if (ASSETS.includes(url.pathname)) {
		event.respondWith(
			(async () => (await (await caches.open(CACHE)).match(url.pathname)) ?? fetch(req))()
		);
	}
});
