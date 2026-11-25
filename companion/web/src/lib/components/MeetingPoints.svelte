<script lang="ts">
	import { m } from '$lib/i18n/paraglide/messages.js';
	import * as meeting from '$lib/meeting.js';
	import { announce } from './Announcer.svelte';

	// The meeting points this app asks (this one, then the ones added here):
	// if one stops, another the computer also lists still finds it.
	let list = $state(meeting.points());
	let text = $state('');
	let failed = $state(false);

	function add(e: SubmitEvent) {
		e.preventDefault();
		failed = !meeting.addPoint(text);
		if (failed) return;
		text = '';
		list = meeting.points();
		announce(m.points_added());
	}

	function remove(url: string) {
		meeting.removePoint(url);
		list = meeting.points();
		announce(m.points_removed());
	}

	const own = typeof location !== 'undefined' ? location.origin : '';
	const show = (url: string) => url.replace(/^https:\/\//, '');
</script>

<section class="card stack" aria-labelledby="points">
	<h2 id="points">{m.points_title()}</h2>
	<p class="muted">{m.points_help()}</p>
	<ul class="stack">
		{#each list as url (url)}
			<li class="stack">
				{#if url === own}
					<span>{m.points_this({ url: show(url) })}</span>
				{:else}
					<span>{show(url)}</span>
					<button type="button" onclick={() => remove(url)}>{m.points_remove({ url: show(url) })}</button>
				{/if}
			</li>
		{/each}
	</ul>
	<form class="stack" onsubmit={add}>
		<label class="stack">
			<span>{m.points_add_label()}</span>
			<input
				type="url"
				bind:value={text}
				autocomplete="off"
				autocapitalize="off"
				spellcheck="false"
				enterkeyhint="done"
				aria-invalid={failed || undefined}
			/>
		</label>
		<button type="submit">{m.points_add_button()}</button>
		{#if failed}<p role="alert">{m.points_add_failed()}</p>{/if}
	</form>
</section>
