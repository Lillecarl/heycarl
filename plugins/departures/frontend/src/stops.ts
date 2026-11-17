import { api } from '@heycarl/sdk';

export const SCOPE = 'departures';
const API = '/api/plugins/departures';
const ZONE = 'Europe/Stockholm';

export type Direction = 1 | 2;

export interface Stop {
	id: number;
	name: string;
	site_id: number;
	site_name: string;
	transport: string | null;
	lines: number[];
	direction: Direction | null;
}

export type StopBody = Omit<Stop, 'id'>;

export interface Site {
	id: number;
	name: string;
	latitude: number | null;
	longitude: number | null;
}

export interface Departure {
	line: number;
	designation: string;
	transport: string;
	group: string | null;
	direction: Direction;
	destination: string;
	state: string;
	scheduled: string;
	expected: string;
	platform: string | null;
	notices: string[];
}

export interface Board {
	site_id: number;
	fetched_at: string;
	departures: Departure[];
	notices: string[];
}

export type Filter = Pick<Stop, 'transport' | 'lines' | 'direction'>;

export const EVERYTHING: Filter = { transport: null, lines: [], direction: null };

export const MODES: Record<string, string> = {
	METRO: 'Metro',
	TRAIN: 'Commuter train',
	TRAM: 'Tram',
	BUS: 'Bus',
	SHIP: 'Boat',
	FERRY: 'Ferry',
	TAXI: 'Taxi'
};

export function boardUrl(siteId: number, filter: Filter): string {
	const query = new URLSearchParams();
	if (filter.transport) query.set('transport', filter.transport);
	for (const line of filter.lines) query.append('line', String(line));
	if (filter.direction) query.set('direction', String(filter.direction));
	return `${API}/sites/${siteId}/departures?${query}`;
}

export function board(url: string): Promise<Board> {
	return api<Board>(url);
}

export async function searchSites(q: string): Promise<Site[]> {
	return (await api<{ sites: Site[] }>(`${API}/sites?q=${encodeURIComponent(q)}`)).sites;
}

export function clock(iso: string): string {
	return new Date(iso).toLocaleTimeString('sv-SE', {
		hour: '2-digit',
		minute: '2-digit',
		timeZone: ZONE
	});
}

export const departureKey = (d: Departure) =>
	`${d.line}/${d.direction}/${d.scheduled}/${d.platform ?? ''}`;

/** Destinations by direction code, in departure order. */
export function destinations(departures: Departure[]): Map<Direction, string[]> {
	const by = new Map<Direction, Set<string>>();
	for (const d of departures) {
		if (!by.has(d.direction)) by.set(d.direction, new Set());
		by.get(d.direction)!.add(d.destination);
	}
	return new Map([...by].sort(([a], [b]) => a - b).map(([k, v]) => [k, [...v]]));
}
