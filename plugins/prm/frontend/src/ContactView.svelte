<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json } from '@heycarl/sdk';
	import Related from '@heycarl/sdk/Related.svelte';
	import Avatar from './Avatar.svelte';
	import {
		ACTIVITY_KINDS,
		DATE_KINDS,
		FIELD_KINDS,
		GIFT_STATES,
		SCOPE,
		type ActivityKind,
		type Contact,
		type ContactDetail,
		type DateKind,
		type Field,
		type FieldKind,
		type GiftState,
		type ImportantDate,
		type Tag,
		type TimelineEntry,
		contactHref,
		fullName,
		inDays,
		localDate,
		today
	} from './types.ts';

	let { id }: { id: number } = $props();

	// One request for the whole contact: PostgREST embeds the children
	// through their foreign keys, tags through contact_tag, and relations
	// through a computed relationship.
	const SELECT = [
		'*',
		'last_contacted',
		'touch_due',
		'contact_field(id,kind,label,value)',
		'important_date(id,kind,label,month,day,year,remind,next_occurrence,days_until,turns)',
		'relations(*)',
		'tag(id,name)',
		'gift(id,title,url,state,note)'
	].join(',');
	const ORDER =
		'&contact_field.order=kind,id&important_date.order=month,day' +
		'&gift.order=created_at.desc&tag.order=name';

	let contact = $state<ContactDetail | null>(null);
	let missing = $state(false);
	let timeline = $state<TimelineEntry[]>([]);
	let kinds = $state<string[]>([]);
	let others = $state<Pick<Contact, 'id' | 'first_name' | 'last_name'>[]>([]);
	let allTags = $state<Tag[]>([]);
	let error = $state<string | null>(null);
	let editing = $state(false);

	async function load(): Promise<void> {
		const [rows, events] = await Promise.all([
			data<ContactDetail[]>(SCOPE, `contact?id=eq.${id}&select=${SELECT}${ORDER}`),
			data<TimelineEntry[]>(SCOPE, `timeline?contact_id=eq.${id}&order=at.desc&limit=100`)
		]);
		missing = rows.length === 0;
		contact = rows[0] ?? null;
		timeline = events;
	}

	async function loadChoices(): Promise<void> {
		[kinds, others, allTags] = await Promise.all([
			data<{ name: string }[]>(SCOPE, 'relationship_kind?select=name&order=sort').then((r) =>
				r.map((k) => k.name)
			),
			data<Pick<Contact, 'id' | 'first_name' | 'last_name'>[]>(
				SCOPE,
				`contact?select=id,first_name,last_name&id=neq.${id}&archived=is.false&order=first_name,last_name`
			),
			data<Tag[]>(SCOPE, 'tag?order=name')
		]);
	}

	async function act(action: () => Promise<unknown>): Promise<void> {
		try {
			error = null;
			await action();
			await load();
		} catch (e) {
			error = String(e);
		}
	}

	const post = (path: string, body: unknown) => data(SCOPE, path, json('POST', body));
	const patch = (path: string, body: unknown) => data(SCOPE, path, json('PATCH', body));
	const del = (path: string) => data(SCOPE, path, { method: 'DELETE' });

	// Profile

	let draft = $state({
		first_name: '',
		last_name: '',
		nickname: '',
		company: '',
		job_title: '',
		description: '',
		stay_in_touch_days: '' as string | number
	});

	function startEdit(): void {
		if (!contact) return;
		draft = {
			first_name: contact.first_name,
			last_name: contact.last_name,
			nickname: contact.nickname,
			company: contact.company,
			job_title: contact.job_title,
			description: contact.description,
			stay_in_touch_days: contact.stay_in_touch_days ?? ''
		};
		editing = true;
	}

	function saveProfile(event: SubmitEvent): void {
		event.preventDefault();
		const days = Number(draft.stay_in_touch_days);
		const body = { ...draft, stay_in_touch_days: days > 0 ? days : null };
		act(() => patch(`contact?id=eq.${id}`, body)).then(() => (editing = false));
	}

	function toggle(column: 'favorite' | 'archived'): void {
		if (contact) act(() => patch(`contact?id=eq.${id}`, { [column]: !contact![column] }));
	}

	async function remove(): Promise<void> {
		if (!contact || !confirm(`Delete ${fullName(contact)} and everything about them?`)) return;
		try {
			await del(`contact?id=eq.${id}`);
			location.hash = '';
		} catch (e) {
			error = String(e);
		}
	}

	// Contact fields

	let fieldKind = $state<FieldKind>('phone');
	let fieldLabel = $state('');
	let fieldValue = $state('');

	function addField(event: SubmitEvent): void {
		event.preventDefault();
		const value = fieldValue.trim();
		if (!value) return;
		const body = { contact_id: id, kind: fieldKind, label: fieldLabel.trim(), value };
		fieldValue = '';
		fieldLabel = '';
		act(() => post('contact_field', body));
	}

	function fieldHref(f: Field): string | null {
		switch (f.kind) {
			case 'email':
				return `mailto:${f.value}`;
			case 'phone':
				return `tel:${f.value.replace(/[^+\d]/g, '')}`;
			case 'address':
				return `https://www.openstreetmap.org/search?query=${encodeURIComponent(f.value)}`;
			case 'website':
			case 'social':
				return /^https?:\/\//.test(f.value) ? f.value : null;
			default:
				return null;
		}
	}

	// Important dates

	let dateKind = $state<DateKind>('birthday');
	let dateLabel = $state('');
	let dateValue = $state('');
	let yearKnown = $state(true);

	function addDate(event: SubmitEvent): void {
		event.preventDefault();
		if (!dateValue) return;
		const [year, month, day] = dateValue.split('-').map(Number);
		const body = {
			contact_id: id,
			kind: dateKind,
			label: dateLabel.trim(),
			month,
			day,
			year: yearKnown ? year : null
		};
		dateValue = '';
		dateLabel = '';
		act(() => post('important_date', body));
	}

	const dayMonth = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'long' });

	function dateText(d: ImportantDate): string {
		const shown = dayMonth.format(new Date(2000, d.month - 1, d.day));
		return d.year ? `${shown} ${d.year}` : shown;
	}

	function turnsText(d: ImportantDate): string {
		if (d.turns === null || d.kind === 'memorial' || d.kind === 'other') return '';
		return d.kind === 'birthday' ? ` · turns ${d.turns}` : ` · ${d.turns} years`;
	}

	// Relationships

	let relKind = $state('friend');
	let relOther = $state<number | ''>('');

	function addRelation(event: SubmitEvent): void {
		event.preventDefault();
		if (relOther === '') return;
		const body = { contact_id: id, other_id: relOther, kind: relKind };
		relOther = '';
		act(() => post('relationship', body));
	}

	// Tags

	let tagName = $state('');

	function addTag(event: SubmitEvent): void {
		event.preventDefault();
		const name = tagName.trim();
		if (!name) return;
		tagName = '';
		act(async () => {
			// Upsert on (owner_sub, name): an existing tag comes back with its id.
			const [tag] = await data<Tag[]>(
				SCOPE,
				'tag?on_conflict=owner_sub,name',
				json('POST', { name }, 'resolution=merge-duplicates,return=representation')
			);
			await post('contact_tag', { contact_id: id, tag_id: tag.id });
			allTags = await data<Tag[]>(SCOPE, 'tag?order=name');
		});
	}

	// Gifts

	let giftTitle = $state('');

	function addGift(event: SubmitEvent): void {
		event.preventDefault();
		const title = giftTitle.trim();
		if (!title) return;
		giftTitle = '';
		act(() => post('gift', { contact_id: id, title }));
	}

	// Activities and notes

	let activityKind = $state<ActivityKind>('call');
	let activityTitle = $state('');
	let activityDate = $state(today());
	let activityWith = $state<number[]>([]);
	let noteBody = $state('');

	function logActivity(event: SubmitEvent): void {
		event.preventDefault();
		const title = activityTitle.trim() || activityKind;
		const body = {
			contact_ids: [id, ...activityWith],
			title,
			kind: activityKind,
			// Today means now; an earlier day has no time to give.
			happened_at: (activityDate === today() ? new Date() : localDate(activityDate)).toISOString()
		};
		activityTitle = '';
		activityWith = [];
		act(() => post('rpc/log_activity', body));
	}

	function addNote(event: SubmitEvent): void {
		event.preventDefault();
		const body = noteBody.trim();
		if (!body) return;
		noteBody = '';
		act(() => post('note', { contact_id: id, body }));
	}

	function removeEntry(t: TimelineEntry): void {
		const table = t.kind === 'note' ? 'note' : t.kind.startsWith('activity') ? 'activity' : 'gift';
		const what = table === 'activity' ? ' for everyone in it' : '';
		if (confirm(`Delete this ${table}${what}?`)) act(() => del(`${table}?id=eq.${t.ref_id}`));
	}

	const when = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });

	onMount(() => {
		load().catch((e) => (error = String(e)));
		loadChoices().catch((e) => (error = String(e)));
	});
</script>

<p><a href="#/">← Everyone</a></p>

{#if error}<p class="error">{error}</p>{/if}

{#if missing}
	<p>No such contact.</p>
{:else if contact}
	{@const c = contact}
	<header class="row-head">
		<Avatar contact={c} big />
		<div class="grow">
			<h1>
				{fullName(c)}
				{#if c.nickname}<span class="muted">“{c.nickname}”</span>{/if}
			</h1>
			{#if c.job_title || c.company}
				<div class="muted">{[c.job_title, c.company].filter(Boolean).join(' at ')}</div>
			{/if}
			<div class="muted">
				{c.last_contacted ? `Last in touch ${when.format(new Date(c.last_contacted))}` : 'No activity yet'}
				{#if c.touch_due}· next by {when.format(localDate(c.touch_due))}{/if}
			</div>
		</div>
		<button class="icon" onclick={() => toggle('favorite')} aria-label="Favourite">
			{c.favorite ? '★' : '☆'}
		</button>
		<button onclick={() => (editing ? (editing = false) : startEdit())}>
			{editing ? 'Cancel' : 'Edit'}
		</button>
	</header>

	{#if editing}
		<form onsubmit={saveProfile}>
			<dl>
				<dt>First name</dt><dd><input bind:value={draft.first_name} required maxlength="100" /></dd>
				<dt>Last name</dt><dd><input bind:value={draft.last_name} maxlength="100" /></dd>
				<dt>Nickname</dt><dd><input bind:value={draft.nickname} maxlength="100" /></dd>
				<dt>Company</dt><dd><input bind:value={draft.company} maxlength="200" /></dd>
				<dt>Job title</dt><dd><input bind:value={draft.job_title} maxlength="200" /></dd>
				<dt>Stay in touch</dt>
				<dd>
					every <input type="number" min="1" bind:value={draft.stay_in_touch_days} style="width: 5rem" />
					days
				</dd>
			</dl>
			<p><textarea bind:value={draft.description} placeholder="How you met, what matters to them…"></textarea></p>
			<div class="row">
				<button type="submit">Save</button>
				<button type="button" onclick={() => toggle('archived')}>
					{c.archived ? 'Unarchive' : 'Archive'}
				</button>
				<button type="button" class="error" onclick={remove}>Delete</button>
			</div>
		</form>
	{:else if c.description}
		<p class="pre">{c.description}</p>
	{/if}

	{#if c.tag.length}
		<p>
			{#each c.tag as t (t.id)}
				<span class="chip">
					{t.name}
					<button
						class="icon"
						aria-label="Remove tag"
						onclick={() => act(() => del(`contact_tag?contact_id=eq.${id}&tag_id=eq.${t.id}`))}
					>×</button>
				</span>
			{/each}
		</p>
	{/if}
	<form class="row" onsubmit={addTag}>
		<input class="grow" list="prm-tags" placeholder="Add a tag" bind:value={tagName} maxlength="50" />
		<datalist id="prm-tags">
			{#each allTags as t (t.id)}<option value={t.name}></option>{/each}
		</datalist>
	</form>

	<section>
		<h2>Contact</h2>
		<ul class="plain">
			{#each c.contact_field as f (f.id)}
				{@const href = fieldHref(f)}
				<li>
					<span class="muted">{f.label || f.kind}</span>
					<span class="grow">
						{#if href}<a {href} target="_blank" rel="noreferrer">{f.value}</a>{:else}{f.value}{/if}
					</span>
					<button class="icon" aria-label="Delete" onclick={() => act(() => del(`contact_field?id=eq.${f.id}`))}>×</button>
				</li>
			{/each}
		</ul>
		<form class="row" onsubmit={addField}>
			<select bind:value={fieldKind}>
				{#each FIELD_KINDS as k (k)}<option value={k}>{k}</option>{/each}
			</select>
			<input placeholder="Label" bind:value={fieldLabel} maxlength="50" style="width: 6rem" />
			<input class="grow" placeholder="Value" bind:value={fieldValue} maxlength="500" />
			<button type="submit">Add</button>
		</form>
	</section>

	<section>
		<h2>Important dates</h2>
		<ul class="plain">
			{#each c.important_date as d (d.id)}
				<li>
					<span class="grow">
						{d.kind === 'other' ? d.label || 'date' : d.kind}
						{#if d.kind !== 'other' && d.label}<span class="muted">({d.label})</span>{/if}
						<span class="muted">· {dateText(d)}</span>
					</span>
					<span class="muted">{inDays(d.days_until)}{turnsText(d)}</span>
					<button
						class="icon"
						title={d.remind ? 'Reminder on' : 'Reminder off'}
						aria-label="Toggle reminder"
						onclick={() => act(() => patch(`important_date?id=eq.${d.id}`, { remind: !d.remind }))}
					>{d.remind ? '🔔' : '🔕'}</button>
					<button class="icon" aria-label="Delete" onclick={() => act(() => del(`important_date?id=eq.${d.id}`))}>×</button>
				</li>
			{/each}
		</ul>
		<form class="row" onsubmit={addDate}>
			<select bind:value={dateKind}>
				{#each DATE_KINDS as k (k)}<option value={k}>{k}</option>{/each}
			</select>
			<input type="date" bind:value={dateValue} required />
			<label class="muted"><input type="checkbox" bind:checked={yearKnown} /> year known</label>
			<input class="grow" placeholder="Label" bind:value={dateLabel} maxlength="100" />
			<button type="submit">Add</button>
		</form>
	</section>

	<section>
		<h2>Relationships</h2>
		<ul class="plain">
			{#each c.relations as r (r.relationship_id + ':' + r.other_id)}
				<li>
					<span class="muted">{r.kind}</span>
					<a class="grow" href={contactHref(r.other_id)}>
						{fullName({ first_name: r.other_first_name, last_name: r.other_last_name })}
					</a>
					<button class="icon" aria-label="Delete" onclick={() => act(() => del(`relationship?id=eq.${r.relationship_id}`))}>×</button>
				</li>
			{/each}
		</ul>
		{#if others.length}
			<form class="row" onsubmit={addRelation}>
				<select class="grow" bind:value={relOther} required>
					<option value="" disabled>Someone…</option>
					{#each others as o (o.id)}<option value={o.id}>{fullName(o)}</option>{/each}
				</select>
				<span class="muted">is their</span>
				<select bind:value={relKind}>
					{#each kinds as k (k)}<option value={k}>{k}</option>{/each}
				</select>
				<button type="submit">Add</button>
			</form>
		{/if}
	</section>

	<section>
		<Related scope={SCOPE} table="contact" key={{ id: c.id }} />
	</section>

	<section>
		<h2>Gifts</h2>
		<ul class="plain">
			{#each c.gift as g (g.id)}
				<li>
					<span class="grow">
						{#if g.url}<a href={g.url} target="_blank" rel="noreferrer">{g.title}</a>{:else}{g.title}{/if}
					</span>
					<select
						value={g.state}
						onchange={(e) =>
							act(() => patch(`gift?id=eq.${g.id}`, { state: e.currentTarget.value as GiftState }))}
					>
						{#each GIFT_STATES as s (s)}<option value={s}>{s}</option>{/each}
					</select>
					<button class="icon" aria-label="Delete" onclick={() => act(() => del(`gift?id=eq.${g.id}`))}>×</button>
				</li>
			{/each}
		</ul>
		<form class="row" onsubmit={addGift}>
			<input class="grow" placeholder="Gift idea" bind:value={giftTitle} maxlength="200" />
			<button type="submit">Add</button>
		</form>
	</section>

	<section>
		<h2>Log</h2>
		<form class="row" onsubmit={logActivity}>
			<select bind:value={activityKind}>
				{#each ACTIVITY_KINDS as k (k)}<option value={k}>{k}</option>{/each}
			</select>
			<input type="date" bind:value={activityDate} required />
			<input class="grow" placeholder="What happened" bind:value={activityTitle} maxlength="200" />
			{#if others.length}
				<span class="muted">with</span>
				<select multiple bind:value={activityWith} aria-label="Also with" class="grow">
					{#each others as o (o.id)}<option value={o.id}>{fullName(o)}</option>{/each}
				</select>
			{/if}
			<button type="submit">Log</button>
		</form>
		<form onsubmit={addNote}>
			<textarea placeholder="Write a note…" bind:value={noteBody}></textarea>
			<button type="submit">Add note</button>
		</form>
	</section>

	<section>
		<h2>Timeline</h2>
		<ul class="plain timeline">
			{#each timeline as t (t.kind + t.ref_id)}
				<li>
					<div class="grow">
						<div class="muted">{when.format(new Date(t.at))} · {t.kind.replace(':', ' · ')}</div>
						{#if t.title}<div>{t.title}</div>{/if}
						{#if t.body}<p class="pre">{t.body}</p>{/if}
					</div>
					<button class="icon" aria-label="Delete" onclick={() => removeEntry(t)}>×</button>
				</li>
			{:else}
				<li class="muted">Nothing yet.</li>
			{/each}
		</ul>
	</section>
{/if}
