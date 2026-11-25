// The iPhone number pad has no Go key: send a pairing code as soon as its
// last digit is typed (or filled in from a message). The field says how many
// digits to wait for (data-digits: 6, or 9 with meeting points). Each code is
// sent once, so a wrong one is not retried until it is changed. The button stays.
export function sendWhenComplete(e: Event) {
	const input = e.currentTarget as HTMLInputElement;
	const digits = input.value.replace(/\D/g, '');
	const want = Number(input.dataset.digits) || 6;
	if (digits.length !== want || input.dataset.sent === digits) return;
	input.dataset.sent = digits;
	input.form?.requestSubmit();
}

/** How many digits the computer's codes have (it answers on its own address). */
export async function codeDigits(): Promise<number> {
	try {
		const res = await fetch('/api/pair-code', { cache: 'no-store' });
		const { digits } = (await res.json()) as { digits?: number };
		return digits === 9 ? 9 : 6;
	} catch {
		return 6;
	}
}
