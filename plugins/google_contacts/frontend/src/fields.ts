// The Google contact, the contact book's contact, and what copy_fields
// (migrations/0001_contacts.sql) would change between them.

export interface Value {
	value: string;
	label: string;
}

export interface Entry {
	id: number;
	account_id: number;
	google_account: string;
	display_name: string;
	given_name: string;
	family_name: string;
	nickname: string;
	company: string;
	job_title: string;
	biography: string;
	birthday_year: number | null;
	birthday_month: number | null;
	birthday_day: number | null;
	emails: Value[];
	phones: Value[];
	addresses: Value[];
	urls: Value[];
	contact_id: number | null;
	contact_first_name: string | null;
	contact_last_name: string | null;
}

export interface Book {
	id: number;
	first_name: string;
	last_name: string;
	nickname: string;
	company: string;
	job_title: string;
	description: string;
	contact_field: { kind: string; label: string; value: string }[];
	important_date: { kind: string; month: number; day: number; year: number | null }[];
}

export interface Suggestion {
	person_id: number;
	contact_id: number;
	first_name: string;
	last_name: string;
	reason: 'email' | 'phone' | 'name';
}

export const BOOK_SELECT =
	'id,first_name,last_name,nickname,company,job_title,description,' +
	'contact_field(kind,label,value),important_date(kind,month,day,year)';

export const REASONS: Record<Suggestion['reason'], string> = {
	email: 'same email',
	phone: 'same phone number',
	name: 'same name'
};

export const entryName = (e: Entry): string =>
	e.display_name || e.emails[0]?.value || 'Unnamed';

export const bookName = (first: string | null, last: string | null): string =>
	[first, last].filter(Boolean).join(' ');

/** One row of the comparison: what Google has, what the book has. */
export interface Row {
	// The name copy_fields takes.
	field: string;
	title: string;
	google: string[];
	book: string[];
	// Whether a copy would change the book.
	differs: boolean;
}

type Text = 'first_name' | 'last_name' | 'nickname' | 'company' | 'job_title' | 'description';

const TEXTS: [Text, string, (e: Entry) => string][] = [
	['first_name', 'First name', (e) => e.given_name],
	['last_name', 'Last name', (e) => e.family_name],
	['nickname', 'Nickname', (e) => e.nickname],
	['company', 'Company', (e) => e.company],
	['job_title', 'Job title', (e) => e.job_title],
	['description', 'About', (e) => e.biography]
];

const LISTS: [string, string, string, (e: Entry) => Value[]][] = [
	['emails', 'Email', 'email', (e) => e.emails],
	['phones', 'Phone', 'phone', (e) => e.phones],
	['addresses', 'Address', 'address', (e) => e.addresses],
	['urls', 'Website', 'website', (e) => e.urls]
];

function day(month: number | null, d: number | null, year: number | null): string | null {
	if (month === null || d === null) return null;
	const md = `${String(month).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
	return year === null ? `--${md}` : `${year}-${md}`;
}

export function compare(e: Entry, b: Book): Row[] {
	const rows: Row[] = TEXTS.map(([field, title, of]) => {
		const google = of(e).trim();
		const book = b[field];
		return {
			field,
			title,
			google: google ? [google] : [],
			book: book ? [book] : [],
			differs: google !== '' && google !== book
		};
	});
	for (const [field, title, kind, of] of LISTS) {
		const book = b.contact_field.filter((f) => f.kind === kind).map((f) => f.value);
		const have = new Set(book.map((v) => v.toLowerCase()));
		const google = of(e).map((v) => v.value);
		rows.push({
			field,
			title,
			google,
			book,
			differs: google.some((v) => !have.has(v.toLowerCase()))
		});
	}
	const birthday = day(e.birthday_month, e.birthday_day, e.birthday_year);
	const birthdays = b.important_date.filter((d) => d.kind === 'birthday');
	rows.push({
		field: 'birthday',
		title: 'Birthday',
		google: birthday ? [birthday] : [],
		book: birthdays.map((d) => day(d.month, d.day, d.year) ?? ''),
		differs:
			birthday !== null &&
			!birthdays.some((d) => d.month === e.birthday_month && d.day === e.birthday_day)
	});
	return rows;
}
