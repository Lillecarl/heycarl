// Rows as PostgREST returns them from schema heycarl_plugin_crm.

export const SCOPE = 'crm';

export interface Contact {
	id: number;
	first_name: string;
	last_name: string;
	nickname: string;
	company: string;
	job_title: string;
	description: string;
	favorite: boolean;
	archived: boolean;
	stay_in_touch_days: number | null;
	last_contacted?: string | null;
	touch_due?: string | null;
}

export type FieldKind = 'email' | 'phone' | 'address' | 'website' | 'social' | 'other';
export const FIELD_KINDS: FieldKind[] = ['email', 'phone', 'address', 'website', 'social', 'other'];

export interface Field {
	id: number;
	kind: FieldKind;
	label: string;
	value: string;
}

export type DateKind = 'birthday' | 'anniversary' | 'memorial' | 'other';
export const DATE_KINDS: DateKind[] = ['birthday', 'anniversary', 'memorial', 'other'];

export interface ImportantDate {
	id: number;
	kind: DateKind;
	label: string;
	month: number;
	day: number;
	year: number | null;
	remind: boolean;
	next_occurrence: string;
	days_until: number;
	turns: number | null;
	contact?: Pick<Contact, 'id' | 'first_name' | 'last_name'>;
}

export interface Relation {
	relationship_id: number;
	other_id: number;
	kind: string;
	other_first_name: string;
	other_last_name: string;
}

export interface Tag {
	id: number;
	name: string;
}

export type GiftState = 'idea' | 'bought' | 'given' | 'received';
export const GIFT_STATES: GiftState[] = ['idea', 'bought', 'given', 'received'];

export interface Gift {
	id: number;
	title: string;
	url: string;
	state: GiftState;
	note: string;
}

export type ActivityKind = 'call' | 'message' | 'meeting' | 'meal' | 'visit' | 'other';
export const ACTIVITY_KINDS: ActivityKind[] = ['call', 'message', 'meeting', 'meal', 'visit', 'other'];

export interface TimelineEntry {
	kind: string;
	ref_id: number;
	at: string;
	title: string;
	body: string;
}

export interface ContactDetail extends Contact {
	contact_field: Field[];
	important_date: ImportantDate[];
	relations: Relation[];
	tag: Tag[];
	gift: Gift[];
}

export function fullName(c: { first_name: string; last_name: string }): string {
	return `${c.first_name} ${c.last_name}`.trim();
}

export function contactHref(id: number): string {
	return `#/contact/${id}`;
}

/** A `date` column ('2026-03-14') as a local date, not midnight UTC. */
export function localDate(iso: string): Date {
	const [y, m, d] = iso.split('-').map(Number);
	return new Date(y, m - 1, d);
}

export function today(): string {
	const d = new Date();
	return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

export function inDays(n: number): string {
	if (n === 0) return 'today';
	if (n === 1) return 'tomorrow';
	return `in ${n} days`;
}
