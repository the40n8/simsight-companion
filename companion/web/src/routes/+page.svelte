<script lang="ts">
	import { codeDigits, sendWhenComplete } from '$lib/code.js';
	import MeetingPoints from '$lib/components/MeetingPoints.svelte';
	import { onMount, tick } from 'svelte';
	import { linkInAddressBar, pairFromText, pairWithCode } from '$lib/session.js';
	import * as meeting from '$lib/meeting.js';
	import { m } from '$lib/i18n/paraglide/messages.js';
	import { live } from '$lib/live.svelte.js';
	import { groupOf, prefs, socialShown } from '$lib/prefs.svelte.js';
	import { eventText } from '$lib/text.js';
	import Announcer, { announce } from '$lib/components/Announcer.svelte';
	import StatusBanner from '$lib/components/StatusBanner.svelte';
	import DialogCard from '$lib/components/DialogCard.svelte';
	import LiveView from '$lib/components/LiveView.svelte';
	import SimsView from '$lib/components/SimsView.svelte';
	import PlayView from '$lib/components/PlayView.svelte';
	import HouseView from '$lib/components/HouseView.svelte';
	import SettingsView from '$lib/components/SettingsView.svelte';
	import HelpView from '$lib/components/HelpView.svelte';
	import SpeedToggle from '$lib/components/SpeedToggle.svelte';
	import Feed from '$lib/components/Feed.svelte';

	type Tab = 'live' | 'play' | 'house' | 'sims' | 'history' | 'settings' | 'help';
	const TABS: { id: Tab; label: () => string }[] = [
		{ id: 'live', label: m.tab_live },
		{ id: 'play', label: m.tab_play },
		{ id: 'house', label: m.tab_house },
		{ id: 'sims', label: m.tab_sims },
		{ id: 'settings', label: m.tab_settings },
		{ id: 'help', label: m.tab_help }
	];
	// History is opened from Live ("Full history"), not a tab of its own:
	// six tabs fit two even rows.
	const VIEWS: { id: Tab; label: () => string }[] = [...TABS, { id: 'history', label: m.tab_history }];
	const TAB_KEY = 'simsight.tab';

	function savedTab(): Tab {
		try {
			const t = localStorage.getItem(TAB_KEY);
			if (VIEWS.some((x) => x.id === t)) return t as Tab;
		} catch {
			// default tab
		}
		return 'live';
	}

	let tab = $state<Tab>(savedTab());

	// An app on the Home Screen cannot be scanned into: paste the link instead.
	let pasted = $state('');
	let pasteFailed = $state(false);
	function pastePairing(e: SubmitEvent) {
		e.preventDefault();
		pasteFailed = !(meeting.hosted ? meeting.takeLink(pasted) : pairFromText(pasted));
		if (!pasteFailed) location.reload();
	}
	// Or the 6-digit code the Mac says when its phone link page opens.
	let code = $state('');
	// 9 digits at meeting points; the computer says, on its own address.
	let digits = $state(meeting.hosted ? 9 : 6);
	if (!meeting.hosted) codeDigits().then((n) => (digits = n));
	let codeBusy = $state(false);
	let codeError = $state('');
	async function codePairing(e: SubmitEvent) {
		e.preventDefault();
		if (codeBusy) return;
		codeBusy = true;
		const out = await (meeting.hosted ? meeting.pairWithCode(code) : pairWithCode(code));
		codeBusy = false;
		if (out === 'ok') return location.reload();
		codeError =
			out === 'locked'
				? m.pair_code_locked()
				: out === 'offline'
					? m.pair_code_offline()
					: m.pair_code_wrong();
	}
	let heading: HTMLHeadingElement | undefined = $state();
	const title = $derived(VIEWS.find((t) => t.id === tab)!.label());

	async function show(next: Tab) {
		tab = next;
		try {
			localStorage.setItem(TAB_KEY, next);
		} catch {
			// not remembered
		}
		await tick();
		// Move VoiceOver to the new screen's title.
		heading?.focus();
	}

	onMount(() => {
		// Drop the pairing token from the address bar once the app has started.
		// SvelteKit's own state object is kept, so its router is unaffected.
		setTimeout(() => {
			if (linkInAddressBar() || meeting.linkInAddressBar())
				history.replaceState(history.state, '', location.pathname + location.search);
		});
		live.onEvent = (e) => {
			const group = groupOf(e.kind);
			if (group === 'social') {
				if (socialShown(e)) announce(eventText(e));
			} else if (group && group !== 'spoken' && prefs.announce[group]) announce(eventText(e));
		};
		live.start();
		return () => live.stop();
	});
</script>

<!-- The screen's name first: VoiceOver reads the title when the app comes back. -->
<svelte:head><title>{title} · {m.page_title()}</title></svelte:head>

<Announcer />

{#if live.status === 'unpaired' || live.status === 'rejected' || live.status === 'waiting' || live.status === 'nopoint'}
	<main class="page stack bare">
		{#if live.status === 'nopoint'}
			<h1>{m.nopoint_title()}</h1>
			<p role="status">{m.status_nopoint()}</p>
			<MeetingPoints />
			<h2>{m.pair_another()}</h2>
		{:else if live.status === 'waiting'}
			<h1>{m.waiting_title()}</h1>
			<p role="status">
				{m.waiting_help({
					names: meeting
						.games()
						.map((g) => g.name || m.waiting_a_mac())
						.join(', ')
				})}
			</p>
			<h2>{m.pair_another()}</h2>
		{:else}
			<h1>{m.pair_title()}</h1>
			<p>{live.status === 'rejected' ? m.pair_rejected() : m.pair_help()}</p>
		{/if}
		<form class="stack" onsubmit={codePairing}>
			<label class="stack">
				<span>{m.pair_code_label({ n: digits })}</span>
				<input
					type="text"
					inputmode="numeric"
					autocomplete="one-time-code"
					maxlength="11"
					bind:value={code}
					data-digits={digits}
					oninput={sendWhenComplete}
					enterkeyhint="go"
				/>
			</label>
			<button type="submit" aria-disabled={codeBusy}>{m.pair_code_button()}</button>
			{#if codeError}<p role="alert">{codeError}</p>{/if}
		</form>
		<form class="stack" onsubmit={pastePairing}>
			<label class="stack">
				<span>{m.pair_paste_label()}</span>
				<input type="url" bind:value={pasted} autocomplete="off" enterkeyhint="go" />
			</label>
			<button type="submit">{m.pair_paste_button()}</button>
			{#if pasteFailed}<p role="alert">{m.pair_paste_failed()}</p>{/if}
		</form>
	</main>
{:else}
	<!-- First in reading order and on screen: pause, then the tabs. At the top so
	     they are never confused with Safari's address bar at the bottom. -->
	<header class="top stack">
		<SpeedToggle />
		<nav aria-label={m.nav_label()}>
			{#each TABS as t (t.id)}
				<button type="button" aria-current={tab === t.id ? 'page' : undefined} onclick={() => show(t.id)}>
					{t.label()}
				</button>
			{/each}
		</nav>
	</header>

	<main class="page stack" aria-labelledby="title">
		<h1 id="title" tabindex="-1" bind:this={heading}>{title}</h1>
		<StatusBanner />
		{#each live.state?.inGame ? live.state.dialogs : [] as dialog (dialog.id)}
			<DialogCard {dialog} />
		{/each}
		{#if tab === 'live'}
			<LiveView onSeeAll={() => show('history')} />
		{:else if tab === 'play'}
			<PlayView />
		{:else if tab === 'house'}
			<HouseView />
		{:else if tab === 'sims'}
			<SimsView />
		{:else if tab === 'history'}
			<button type="button" onclick={() => show('live')}>{m.back_to_live()}</button>
			<Feed />
		{:else if tab === 'settings'}
			<SettingsView />
		{:else}
			<HelpView />
		{/if}
	</main>
{/if}

<style>
	.top {
		position: sticky;
		top: 0;
		z-index: 2;
		gap: 0.5rem;
		padding: calc(env(safe-area-inset-top) + 0.5rem) calc(env(safe-area-inset-right) + var(--gap)) 0.5rem
			calc(env(safe-area-inset-left) + var(--gap));
		background: var(--surface);
		border-bottom: 1px solid var(--line);
	}
	.page {
		max-width: 40rem;
		margin: 0 auto;
		padding: 1rem calc(env(safe-area-inset-right) + var(--gap)) calc(env(safe-area-inset-bottom) + 2rem)
			calc(env(safe-area-inset-left) + var(--gap));
	}
	/* No top bar on this screen: keep clear of the clock and the notch
	   (full-screen Home Screen app); the top bar does it on the others. */
	.bare {
		padding-top: calc(env(safe-area-inset-top) + 1rem);
	}
	h1:focus {
		outline: none;
	}
	nav {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 0.25rem;
	}
	nav button {
		border: none;
		background: transparent;
		font-weight: 600;
		padding: 0.5rem 0.25rem;
	}
	nav button[aria-current='page'] {
		background: var(--accent);
		color: var(--accent-ink);
	}
</style>
