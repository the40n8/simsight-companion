<script lang="ts">
	import { sendWhenComplete } from '$lib/code.js';
	import { m } from '$lib/i18n/paraglide/messages.js';
	import { LANGUAGES, changeLocale, i18n, isLocale } from '$lib/i18n/locale.svelte.js';
	import {
		GROUPS,
		SOCIAL_LEVELS,
		prefs,
		savePrefs,
		type Group,
		type SocialLevel
	} from '$lib/prefs.svelte.js';
	import { forgetToken, loadToken } from '$lib/session.js';
	import * as meeting from '$lib/meeting.js';
	import { client, run } from '$lib/client.js';
	import { computer, live } from '$lib/live.svelte.js';
	import { reasonText } from '$lib/text.js';
	import type { Narration } from '$lib/gen/simsight/v1/game_pb.js';
	import { announce } from './Announcer.svelte';

	const narration = $derived(live.state?.narration);
	let busy = $state(false);
	async function setNarration(change: Partial<Narration>) {
		if (busy || !live.gameConnected) return;
		const current = narration ?? { macVoice: true, simActions: true };
		busy = true;
		const out = await run(() =>
			client.setNarration({
				narration: { macVoice: current.macVoice, simActions: current.simActions, ...change }
			})
		);
		busy = false;
		announce(out.ok ? m.done() : m.failed({ reason: reasonText(out.reason) }));
	}

	const labels: Record<Group, () => string> = {
		messages: m.kind_messages,
		mood: m.kind_mood,
		moodlet: m.kind_moodlet,
		need: m.kind_need,
		action: m.kind_action,
		game: m.kind_game
	};

	const socialLabels: Record<SocialLevel, () => string> = {
		off: m.social_off,
		labels: m.social_labels,
		normal: m.social_normal,
		all: m.social_all
	};

	// What the Mac voice says by itself: kept on the Mac, the same for every phone.
	const macAnnounce = $derived({
		groups: live.state?.macAnnounce?.groups ?? [],
		social: live.state?.macAnnounce?.social || 'labels'
	});
	async function setMacAnnounce(next: { groups: string[]; social: string }) {
		const out = await run(() => client.setMacAnnounce({ macAnnounce: next }));
		announce(out.ok ? m.done() : m.failed({ reason: reasonText(out.reason) }));
	}
	function setMacGroup(group: Group, on: boolean) {
		const groups = macAnnounce.groups.filter((g) => g !== group);
		void setMacAnnounce({ ...macAnnounce, groups: on ? [...groups, group] : groups });
	}

	// For the app on the Home Screen (its own storage): paste this link there once.
	async function copyLink() {
		const game = live.game;
		const token = meeting.hosted ? game?.token : loadToken();
		if (!token) return;
		const link = meeting.hosted
			? `${location.origin}/#m=${game!.id}&k=${token}`
			: `${location.origin}/#k=${token}`;
		try {
			await navigator.clipboard.writeText(link);
			announce(m.pairing_copied());
		} catch {
			announce(m.prompt_copy_failed());
		}
	}

	function unpair() {
		if (meeting.hosted) {
			if (live.game) meeting.forgetGame(live.game.id);
		} else forgetToken();
		location.reload();
	}

	// At a meeting point: the paired computers, and pairing one more.
	let paired = $state(meeting.hosted ? meeting.games() : []);
	function forget(id: string, name: string) {
		meeting.forgetGame(id);
		paired = meeting.games();
		announce(m.forgot_mac({ name: name || m.waiting_a_mac() }));
		if (live.game?.id === id) location.reload();
	}
	let code = $state('');
	let codeBusy = $state(false);
	async function pairAnother(e: SubmitEvent) {
		e.preventDefault();
		if (codeBusy) return;
		codeBusy = true;
		const out = await meeting.pairWithCode(code);
		codeBusy = false;
		paired = meeting.games();
		if (out === 'ok') {
			code = '';
			announce(m.paired_with({ name: paired[0]?.name || m.waiting_a_mac() }));
		} else
			announce(
				out === 'locked'
					? m.pair_code_locked()
					: out === 'offline'
						? m.pair_code_offline()
						: m.pair_code_wrong()
			);
	}
</script>

<section class="card stack" aria-labelledby="lang">
	<h2 id="lang">{m.language()}</h2>
	<select
		aria-labelledby="lang"
		value={i18n.locale}
		onchange={(e) => isLocale(e.currentTarget.value) && changeLocale(e.currentTarget.value)}
	>
		{#each LANGUAGES as l (l.locale)}
			<option value={l.locale} lang={l.locale}>{l.name}</option>
		{/each}
	</select>
</section>

<fieldset class="card stack">
	<legend><h2>{m.announce_title()}</h2></legend>
	<p class="muted">{m.announce_help()}</p>
	{#each GROUPS as group (group)}
		<label class="check">
			<input type="checkbox" bind:checked={prefs.announce[group]} onchange={savePrefs} />
			{labels[group]()}
		</label>
	{/each}
	<label class="check">
		<input type="checkbox" bind:checked={prefs.showSpoken} onchange={savePrefs} />
		{m.show_spoken(computer())}
	</label>
</fieldset>

<fieldset class="card stack">
	<legend><h2>{m.social_title()}</h2></legend>
	<p class="muted">{m.social_help()}</p>
	{#each SOCIAL_LEVELS as level (level)}
		<label class="check">
			<input type="radio" name="social" value={level} bind:group={prefs.social} onchange={savePrefs} />
			{socialLabels[level]()}
		</label>
	{/each}
</fieldset>

<section class="card stack" aria-labelledby="playing">
	<h2 id="playing">{m.playing_title()}</h2>
	<label class="check">
		<input type="checkbox" bind:checked={prefs.autoPause} onchange={savePrefs} />
		{m.auto_pause()}
	</label>
	<p class="muted">{m.auto_pause_help()}</p>
</section>

<section class="card stack" aria-labelledby="narration">
	<h2 id="narration">{m.narration_title(computer())}</h2>
	<button
		type="button"
		aria-pressed={narration?.macVoice ?? false}
		aria-disabled={busy || !live.gameConnected}
		onclick={() => setNarration({ macVoice: !narration?.macVoice })}>{m.mac_voice(computer())}</button
	>
	<button
		type="button"
		aria-pressed={narration?.simActions ?? false}
		aria-disabled={busy || !live.gameConnected}
		onclick={() => setNarration({ simActions: !narration?.simActions })}>{m.sim_actions(computer())}</button
	>
</section>

<fieldset class="card stack">
	<legend><h2>{m.mac_announce_title(computer())}</h2></legend>
	<p class="muted">{m.mac_announce_help(computer())}</p>
	{#each GROUPS as group (group)}
		<label class="check">
			<input
				type="checkbox"
				checked={macAnnounce.groups.includes(group)}
				aria-disabled={!live.gameConnected}
				onchange={(e) => setMacGroup(group, e.currentTarget.checked)}
			/>
			{labels[group]()}
		</label>
	{/each}
	<p>{m.social_title()}</p>
	{#each SOCIAL_LEVELS as level (level)}
		<label class="check">
			<input
				type="radio"
				name="mac-social"
				value={level}
				checked={macAnnounce.social === level}
				onchange={() => setMacAnnounce({ ...macAnnounce, social: level })}
			/>
			{socialLabels[level]()}
		</label>
	{/each}
</fieldset>

<section class="card stack" aria-labelledby="pairing">
	<h2 id="pairing">{m.pairing_title()}</h2>
	<p class="muted">{m.pairing_help()}</p>
	<button type="button" onclick={copyLink}>{m.copy_pairing_link()}</button>
	<button type="button" onclick={unpair}>{m.unpair(computer())}</button>
</section>

{#if meeting.hosted}
	<section class="card stack" aria-labelledby="computers">
		<h2 id="computers">{m.computers_title()}</h2>
		<p class="muted">{m.computers_help()}</p>
		<ul class="stack">
			{#each paired as g (g.id)}
				<li class="stack">
					<span
						>{g.name || m.waiting_a_mac()}{#if live.game?.id === g.id}{` · ${m.computer_in_use()}`}{/if}</span
					>
					<button type="button" onclick={() => forget(g.id, g.name)}
						>{m.forget_mac({ name: g.name || m.waiting_a_mac() })}</button
					>
				</li>
			{/each}
		</ul>
		<form class="stack" onsubmit={pairAnother}>
			<label class="stack">
				<span>{m.pair_another_code()}</span>
				<input
					type="text"
					inputmode="numeric"
					autocomplete="one-time-code"
					maxlength="11"
					bind:value={code}
					oninput={sendWhenComplete}
					enterkeyhint="go"
				/>
			</label>
			<button type="submit" aria-disabled={codeBusy}>{m.pair_code_button()}</button>
		</form>
	</section>
{/if}

<style>
	select {
		font: inherit;
		min-height: var(--tap);
		border-radius: var(--r);
		padding: 0 0.75rem;
		background: var(--surface-2);
		color: var(--text);
		border: 2px solid var(--line);
	}
	fieldset {
		margin: 0;
	}
	legend {
		padding: 0;
		margin-bottom: 0.5rem;
	}
	.check {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		min-height: var(--tap);
	}
	.check input {
		width: 1.5rem;
		height: 1.5rem;
		accent-color: var(--accent);
	}
</style>
