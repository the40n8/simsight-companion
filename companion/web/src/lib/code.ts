// The iPhone number pad has no Go key: send a pairing code as soon as its
// 6th digit is typed (or filled in from a message). Each code is sent once,
// so a wrong one is not retried until it is changed. The button stays.
export function sendWhenComplete(e: Event) {
	const input = e.currentTarget as HTMLInputElement;
	const digits = input.value.replace(/\D/g, '');
	if (digits.length !== 6 || input.dataset.sent === digits) return;
	input.dataset.sent = digits;
	input.form?.requestSubmit();
}
