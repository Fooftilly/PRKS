// Model of the pure rules in frontend-app/src/sync/reconcile.ts (#1472).
//
// A JS value in a revision position is Valid(n) or Invalid. A missing
// heldRevision (undefined or null) is Absent. A time that does not parse is
// NoTime. The model is compared with production only after its tags are
// erased; production values carry no tags.

module Reconcile {
  datatype Option<T> = None | Some(value: T)

  datatype Rev = Valid(n: nat) | Invalid
  datatype Held = Absent | Present(r: Rev)
  datatype Time = At(t: int) | NoTime
  datatype Evidence = Evidence(ack: Rev, held: Held, observed: Time, created: Time, agrees: bool)
  datatype Plan = Superseded | Unchanged | Apply | Unproven
  datatype Outcome = Return(plan: Plan) | Throws

  predicate ReadBefore(observed: Time, created: Time) {
    observed.At? && created.At? && observed.t < created.t
  }

  function DecideAck(e: Evidence): Outcome {
    if e.ack.Invalid? then Throws
    else if e.held.Present? then
      if e.held.r.Invalid? then Return(Unproven)
      else if e.ack.n < e.held.r.n then Return(Superseded)
      else Return(Apply)
    else if e.agrees then Return(Apply)
    else if ReadBefore(e.observed, e.created) then Return(Apply)
    else Return(Unproven)
  }

  // The production composition: a sibling check through DecideAck, then
  // agreement, then DecideAck without the sibling.
  function PlanAck(e: Evidence): Outcome {
    if e.held.Present? && DecideAck(Evidence(e.ack, e.held, NoTime, NoTime, false)).Throws? then Throws
    else if e.held.Present? && DecideAck(Evidence(e.ack, e.held, NoTime, NoTime, false)) == Return(Superseded) then Return(Superseded)
    else if e.agrees then Return(Unchanged)
    else DecideAck(Evidence(e.ack, Absent, e.observed, e.created, false))
  }

  lemma PlanAckTable(e: Evidence)
    ensures PlanAck(e) ==
      if e.held.Present? && e.ack.Invalid? then Throws
      else if e.held.Present? && e.held.r.Valid? && e.ack.n < e.held.r.n then Return(Superseded)
      else if e.agrees then Return(Unchanged)
      else if e.ack.Invalid? then Throws
      else if ReadBefore(e.observed, e.created) then Return(Apply)
      else Return(Unproven)
  {}

  lemma DecideAckTable(e: Evidence)
    ensures DecideAck(e) ==
      if e.ack.Invalid? then Throws
      else if e.held.Present? && e.held.r.Invalid? then Return(Unproven)
      else if e.held.Present? && e.ack.n < e.held.r.n then Return(Superseded)
      else if e.held.Present? then Return(Apply)
      else if e.agrees || ReadBefore(e.observed, e.created) then Return(Apply)
      else Return(Unproven)
  {}

  lemma NoProofNoApply(e: Evidence)
    ensures PlanAck(e) == Return(Apply) ==>
      !e.agrees && ReadBefore(e.observed, e.created) &&
      !(e.held.Present? && e.held.r.Valid? && e.ack.Valid? && e.ack.n < e.held.r.n)
  {}

  lemma SupersededOnlyByAnOlderAck(e: Evidence)
    ensures PlanAck(e) == Return(Superseded) ==>
      e.held.Present? && e.held.r.Valid? && e.ack.Valid? && e.ack.n < e.held.r.n
  {}

  lemma PlanAndDecideDifferOnlyWithHeldOrAgreement(e: Evidence)
    ensures e.held.Absent? && !e.agrees ==> PlanAck(e) == DecideAck(e)
  {}

  // Finding 1 of the #1472 review: PlanAck skips validation only on the
  // agreement path without a sibling revision. With a valid ACK the two
  // functions agree on validation.
  lemma ValidationGap(e: Evidence)
    ensures e.ack.Valid? ==> !PlanAck(e).Throws? && !DecideAck(e).Throws?
    ensures e.ack.Invalid? ==> DecideAck(e).Throws?
    ensures e.ack.Invalid? ==> (PlanAck(e).Throws? <==> !(e.agrees && e.held.Absent?))
  {}

  // ---- Edit bases ----

  // A field entry as a read or the cache holds it; Broken is any value that
  // is not {value: string, revision: Revision}.
  datatype Entry<V> = Ok(value: V, revision: nat, pendingOp: Option<string>, ackOp: Option<string>) | Broken

  // This device's own unsent edit for a field. NoOwn is a missing key;
  // OwnBroken is null or an invalid entry.
  datatype Own<V> = NoOwn | OwnBroken | Own(value: V, revision: nat, pendingOp: Option<string>)

  datatype Base<V> = Base(value: V, revision: nat, pending: Option<V>, pendingOp: Option<string>, ackOp: Option<string>)

  // Provenance of a pin, computed from the inputs only.
  datatype Tag = ReadPair | MaxRead | MaxWrite | WriteBase | Unknown

  function PinField<V(==)>(shown: V, entry: Entry<V>, own: Own<V>): (Option<Base<V>>, Tag) {
    if entry.Broken? then (None, Unknown)
    else if own.NoOwn? then
      if entry.value == shown then (Some(Base(shown, entry.revision, None, None, None)), ReadPair) else (None, Unknown)
    else if own.OwnBroken? || own.value != shown then (None, Unknown)
    else if own.value == entry.value then
      if entry.revision >= own.revision then (Some(Base(entry.value, entry.revision, None, own.pendingOp, None)), MaxRead)
      else (Some(Base(entry.value, own.revision, None, own.pendingOp, None)), MaxWrite)
    else if entry.revision == own.revision then
      (Some(Base(entry.value, own.revision, Some(own.value), own.pendingOp, None)), WriteBase)
    else (None, Unknown)
  }

  // B2: every pin is one of the four tagged kinds, and nothing else.
  lemma PinKinds<V>(shown: V, entry: Entry<V>, own: Own<V>)
    ensures var (pin, tag) := PinField(shown, entry, own);
      (pin.None? <==> tag == Unknown) &&
      (tag == ReadPair ==> entry.Ok? && own.NoOwn? && pin == Some(Base(shown, entry.revision, None, None, None))) &&
      (tag == MaxRead ==> (entry.Ok? && own.Own? && own.value == shown && entry.value == shown &&
        entry.revision >= own.revision && pin == Some(Base(shown, entry.revision, None, own.pendingOp, None)))) &&
      (tag == MaxWrite ==> (entry.Ok? && own.Own? && own.value == shown && entry.value == shown &&
        own.revision > entry.revision && pin == Some(Base(shown, own.revision, None, own.pendingOp, None)))) &&
      (tag == WriteBase ==> (entry.Ok? && own.Own? && own.value == shown && entry.value != shown &&
        entry.revision == own.revision && pin == Some(Base(entry.value, entry.revision, Some(shown), own.pendingOp, None))))
  {}

  // A read-tagged pin is a pair the read held. A MaxWrite pin is not.
  lemma ReadTaggedPinsAreReadPairs<V>(shown: V, entry: Entry<V>, own: Own<V>)
    ensures var (pin, tag) := PinField(shown, entry, own);
      (tag == ReadPair || tag == MaxRead) ==>
        entry.Ok? && pin.Some? && pin.value.value == entry.value && pin.value.revision == entry.revision
  {}

  // A pin shows the form's value, or carries it as this device's pending edit.
  lemma PinShowsTheForm<V>(shown: V, entry: Entry<V>, own: Own<V>)
    ensures var (pin, tag) := PinField(shown, entry, own);
      pin.Some? ==> pin.value.value == shown || pin.value.pending == Some(shown)
  {}

  // B1: pinnedEdits over maps. The model keeps `unknown` as a set; the
  // differential test compares production's array as a set and checks its
  // order separately.
  function PinnedChanges<V(==)>(shown: map<string, V>, draft: map<string, V>): map<string, V> {
    map f | f in shown && f in draft && draft[f] != shown[f] :: draft[f]
  }

  function PinnedBases<V>(shown: map<string, V>, pinned: map<string, Option<Base<V>>>): map<string, Base<V>> {
    map f | f in shown && f in pinned && pinned[f].Some? :: pinned[f].value
  }

  function PinnedUnknown<V(==)>(shown: map<string, V>, draft: map<string, V>, pinned: map<string, Option<Base<V>>>): set<string> {
    set f | f in PinnedChanges(shown, draft) && !(f in pinned && pinned[f].Some?)
  }

  lemma PinnedEditsSendExactlyTheChangedFields<V>(shown: map<string, V>, draft: map<string, V>, pinned: map<string, Option<Base<V>>>)
    ensures forall f :: f in PinnedChanges(shown, draft) <==> (f in shown && f in draft && draft[f] != shown[f])
    ensures forall f :: f in PinnedChanges(shown, draft) ==> f in PinnedBases(shown, pinned) || f in PinnedUnknown(shown, draft, pinned)
    ensures forall f :: f in PinnedUnknown(shown, draft, pinned) ==> f in PinnedChanges(shown, draft) && f !in PinnedBases(shown, pinned)
  {}

  // B3: the entry an edit-base snapshot keeps when a read finishes.
  function NewerEditBase<V>(held: Entry<V>, read: Entry<V>): Entry<V> {
    if held.Broken? || read.Broken? then read
    else if held.revision > read.revision then held
    else if held.revision == read.revision && held.ackOp.Some? then read.(ackOp := held.ackOp)
    else read
  }

  lemma NewerEditBaseKeepsNewerState<V>(held: Entry<V>, read: Entry<V>)
    ensures held.Ok? && read.Ok? ==>
      var got := NewerEditBase(held, read);
      got.Ok? &&
      got.revision == (if held.revision > read.revision then held.revision else read.revision) &&
      (held.revision >= read.revision && held.ackOp.Some? ==> got.ackOp == held.ackOp) &&
      (got.revision == read.revision ==> got.value == read.value)
  {}

  // C1: an edit base after this device's own write is acknowledged.
  datatype AckResult<V> = Keep | Clear | Pair(value: V, revision: nat)

  function AcknowledgeEditBase<V>(entry: Entry<V>, value: V, baseRevision: Rev, ackRevision: nat): AckResult<V> {
    if entry.Broken? then Clear
    else if entry.revision > ackRevision then Keep
    else if entry.revision == ackRevision then
      Pair(entry.value, entry.revision)
    else if baseRevision.Valid? && entry.revision == baseRevision.n then Pair(value, ackRevision)
    else Clear
  }

  lemma AcknowledgedPairIsTheWriteAtTheAck<V>(entry: Entry<V>, value: V, baseRevision: Rev, ackRevision: nat)
    ensures var got := AcknowledgeEditBase(entry, value, baseRevision, ackRevision);
      (got.Keep? <==> entry.Ok? && entry.revision > ackRevision) &&
      (got.Pair? ==> (entry.Ok? && got.revision == ackRevision &&
        (entry.revision == ackRevision ==> got.value == entry.value) &&
        (entry.revision < ackRevision ==> got.value == value && baseRevision == Valid(entry.revision)))) &&
      (got.Clear? ==> entry.Broken? || (entry.revision < ackRevision && baseRevision != Valid(entry.revision)))
  {}
}
