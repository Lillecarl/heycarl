/**
 * A failed request. Every /api error body has PostgREST's shape,
 * `{code, message, details, hint}` (DESIGN.md "Request path"); the parser
 * also takes FastAPI's `{detail}`, plain text and an empty body, for answers
 * from outside the kernel (Caddy, an older kernel). `status` 0: no answer.
 */
export class ApiError extends Error {
	override readonly name = 'ApiError';

	constructor(
		readonly status: number,
		message: string,
		readonly code: string | null = null,
		readonly details: unknown = null,
		readonly hint: string | null = null,
		options?: ErrorOptions
	) {
		super(message, options);
	}
}

const UNREACHABLE = 'Could not reach Hey Carl. Check the connection and try again.';

export function unreachable(cause: unknown): ApiError {
	return new ApiError(0, UNREACHABLE, null, null, null, { cause });
}

function field(body: Record<string, unknown>, name: string): string | null {
	const value = body[name];
	return typeof value === 'string' && value ? value : null;
}

export function parseError(status: number, contentType: string | null, text: string): ApiError {
	if (contentType?.includes('json')) {
		try {
			const body: unknown = JSON.parse(text);
			if (body && typeof body === 'object' && !Array.isArray(body)) {
				const fields = body as Record<string, unknown>;
				const message = field(fields, 'message');
				if (message !== null) {
					return new ApiError(
						status,
						message,
						field(fields, 'code'),
						fields.details ?? null,
						field(fields, 'hint')
					);
				}
				const detail = fields.detail;
				if (typeof detail === 'string' && detail) return new ApiError(status, detail);
				if (detail != null) return new ApiError(status, '', null, detail);
			}
		} catch {
			// A body that claims JSON and is not: the status speaks.
		}
		return new ApiError(status, '');
	}
	const plain = text.trim();
	const usable = plain && !contentType?.includes('html') && plain.length <= 300;
	return new ApiError(status, usable ? plain : '');
}

const BY_STATUS: Record<number, string> = {
	400: 'The request was not accepted.',
	401: 'Signing in again…',
	403: 'This is not allowed.',
	404: 'Not found. It may have been deleted.',
	409: 'This conflicts with the current data. Reload and try again.',
	413: 'This is too large.',
	422: 'Some of the values are not valid.',
	429: 'Too many tries. Wait a moment and try again.',
	502: 'Hey Carl is not answering. Try again soon.',
	503: 'Hey Carl is not answering. Try again soon.',
	504: 'Hey Carl took too long to answer. Try again.'
};

// SQLSTATE classes whose PostgreSQL wording names constraints, not things.
const BY_SQLSTATE: Record<string, string> = {
	'23505': 'That already exists.',
	'23503': 'Something it refers to is missing, or something still refers to it.',
	'23502': 'A required value is missing.',
	'23514': 'A value is not allowed.',
	'22P02': 'A value has the wrong form.',
	'42501': 'This is not allowed.'
};

function sentence(text: string): string {
	const first = text.charAt(0).toUpperCase() + text.slice(1);
	return /[.!?…]$/.test(first) ? first : `${first}.`;
}

function fromStatus(status: number): string {
	return BY_STATUS[status] ?? (status >= 500 ? 'Hey Carl had a problem. Try again.' : 'The request failed.');
}

function apiText(e: ApiError): string {
	if (e.status === 0) return e.message;
	if (e.code && e.code in BY_SQLSTATE) return BY_SQLSTATE[e.code];
	// PGRST: PostgREST's own wording, about tables and columns. 5xx without a
	// code: a crash's text, not a message for a person.
	const technical = e.code?.startsWith('PGRST') || (e.status >= 500 && !e.code);
	if (technical || !e.message) return fromStatus(e.status);
	return sentence(e.message);
}

/**
 * The sentence to show a person for any thrown value. An `Error` with a
 * `cause` reads as its message, then the cause: `new Error('Could not save',
 * { cause: e })`.
 */
export function errorText(error: unknown): string {
	if (error instanceof ApiError) return apiText(error);
	if (error instanceof Error) {
		if (error.cause !== undefined) return `${error.message}: ${errorText(error.cause)}`;
		return error.message ? sentence(error.message) : 'Something went wrong.';
	}
	return typeof error === 'string' && error ? sentence(error) : 'Something went wrong.';
}
