// The device's position, read once when the person asks. Hey Carl never
// watches it and never stores it: send it in a POST body, so that no URL,
// and so no access log, carries it.

export interface Position {
	latitude: number;
	longitude: number;
	/** Metres, as the device estimates it. */
	accuracy: number;
}

/** Thrown when the device gives no position; `code` is the browser's. */
export class PositionError extends Error {
	constructor(
		readonly code: 'denied' | 'unavailable' | 'timeout' | 'unsupported',
		message: string
	) {
		super(message);
		this.name = 'PositionError';
	}
}

const TEXT: Record<number, [PositionError['code'], string]> = {
	1: ['denied', 'Hey Carl may not read your location. Allow it in the site settings of your browser.'],
	2: ['unavailable', 'Your device could not find its location.'],
	3: ['timeout', 'Your device took too long to find its location. Try again.']
};

export function position(options: { timeout?: number; maxAge?: number } = {}): Promise<Position> {
	if (!('geolocation' in navigator)) {
		return Promise.reject(
			new PositionError('unsupported', 'This browser cannot read your location.')
		);
	}
	return new Promise((resolve, reject) => {
		navigator.geolocation.getCurrentPosition(
			(p) =>
				resolve({
					latitude: p.coords.latitude,
					longitude: p.coords.longitude,
					accuracy: p.coords.accuracy
				}),
			(e) => {
				const [code, text] = TEXT[e.code] ?? TEXT[2];
				reject(new PositionError(code, text));
			},
			{
				enableHighAccuracy: true,
				timeout: options.timeout ?? 20_000,
				maximumAge: options.maxAge ?? 30_000
			}
		);
	});
}
