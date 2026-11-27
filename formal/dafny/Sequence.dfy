// Sequence model of one field on one device (#1472, section S).
//
// M0 is current behaviour: the wall clock can step back (#1473), a second
// sender can reconcile a stored ACK late (#1072), and edit bases are stamped
// with the client payload. M1 is the corrected model: no clock step (O1), one
// sender per queue (O2) and canonical stamps. Claims about current behaviour
// use M0 only.
//
// The server is an oracle: it stores Canonical(value), applies a write only on
// its current revision, and answers a write that changes nothing with a no-op
// ACK at the current revision. A read holds a real server state. The cache
// write after a read may fail. Each event advances the clock by one, except
// ClockStep. Fields are independent in production, so one field is modelled;
// a list row shares its snapshot's read time, which is the case modelled here.

include "Reconcile.dfy"

module Sequence {
  import opened Reconcile

  datatype Model = M0 | M1
  datatype Val = A | B | C | SpacedB

  function Canonical(v: Val): Val { if v == SpacedB then B else v }

  function Stamp(m: Model, v: Val): Val { if m == M0 then v else Canonical(v) }

  // origin: the value the user saw at `base` when this intent was first formed.
  // curAtCreate: the server's revision when the operation was created.
  datatype Op = Op(id: nat, value: Val, base: nat, created: int, curAtCreate: nat, origin: Val)

  datatype Cached = Cached(value: Val, observed: int, rev: nat)

  datatype State = State(
    history: seq<Val>,
    now: int,
    cache: Option<Cached>,
    proj: Option<nat>,
    editBase: Entry<Val>,
    op: Option<Op>,
    ops: map<nat, Op>,
    ledger: map<nat, nat>,
    nextId: nat)

  datatype Event =
    | Read(r: nat, stored: bool)
    | ReadProjection(r: nat)
    | ReadEditBase(r: nat, stored: bool)
    | Save(v: Val)
    | Resave(v: Val)
    | Send
    | OtherWrite(v: Val)
    | Deliver
    | Replay(id: nat)
    | ClockStep(to: int)
    | Reload

  function Init(): State {
    State([A], 0, None, None, Broken, None, map[], map[], 0)
  }

  predicate Wf(s: State) { |s.history| >= 1 }

  function Cur(s: State): nat requires Wf(s) { |s.history| - 1 }

  predicate Enabled(m: Model, s: State, e: Event) {
    Wf(s) &&
    (match e
    case Read(r, _) => r <= Cur(s)
    case ReadProjection(r) => r <= Cur(s)
    case ReadEditBase(r, _) => r <= Cur(s)
    case Save(_) => s.op.None? && s.editBase.Ok? && s.editBase.revision <= Cur(s)
    case Resave(_) => s.op.Some? && s.op.value.id !in s.ledger
    case Send => s.op.Some? && s.op.value.id !in s.ledger && s.op.value.base <= Cur(s)
    case OtherWrite(_) => true
    case Deliver => s.op.Some? && s.op.value.id in s.ledger && s.ledger[s.op.value.id] <= Cur(s)
    case Replay(id) => m == M0 && id in s.ledger && id in s.ops && s.ledger[id] <= Cur(s)
    case ClockStep(_) => m == M0
    case Reload => true)
  }

  function EditBaseAfterAck(m: Model, s: State, o: Op, a: nat): Entry<Val> {
    match AcknowledgeEditBase(s.editBase, Stamp(m, o.value), Valid(o.base), a)
    case Keep => s.editBase
    case Clear => Broken
    case Pair(v, r) => Ok(v, r, None, Some("ack"))
  }

  function CacheAfterAck(s: State, o: Op, a: nat): Option<Cached> {
    match s.cache
    case None => None
    case Some(c) =>
      var plan := PlanAck(Evidence(Valid(a), Absent, At(c.observed), At(o.created), c.value == Canonical(o.value)));
      if plan == Return(Apply) then Some(Cached(Canonical(o.value), c.observed, a))
      else if plan == Return(Unchanged) then s.cache
      else None
  }

  // reconcileFolderField: the projection decides first, then the cached value
  // under planAck (patchProvenEntity keeps the old read time), then the edit base.
  function Reconciled(m: Model, s: State, o: Op, a: nat): State {
    if s.proj.Some? && a < s.proj.value then s
    else s.(
      proj := if s.proj.Some? then Some(a) else None,
      cache := CacheAfterAck(s, o, a),
      editBase := EditBaseAfterAck(m, s, o, a))
  }

  function Created(s: State, value: Val, base: nat, origin: Val): State requires Wf(s) {
    var o := Op(s.nextId, value, base, s.now, Cur(s), origin);
    s.(op := Some(o), ops := s.ops[s.nextId := o], nextId := s.nextId + 1)
  }

  function Received(s: State): State requires Wf(s) && s.op.Some? {
    var o := s.op.value;
    if o.base != Cur(s) then s.(op := None)
    else if Canonical(o.value) == s.history[Cur(s)] then s.(ledger := s.ledger[o.id := Cur(s)])
    else s.(history := s.history + [Canonical(o.value)], ledger := s.ledger[o.id := Cur(s) + 1])
  }

  function Apply1(m: Model, s: State, e: Event): State requires Enabled(m, s, e) {
    match e
    case Read(r, stored) => if stored then s.(cache := Some(Cached(s.history[r], s.now, r))) else s
    case ReadProjection(r) => s.(proj := Some(r))
    case ReadEditBase(r, stored) =>
      if stored then s.(editBase := NewerEditBase(s.editBase, Ok(s.history[r], r, None, None))) else s
    case Save(v) => Created(s, v, s.editBase.revision, s.editBase.value)
    case Resave(v) => Created(s, v, s.op.value.base, s.op.value.origin)
    case Send => Received(s)
    case OtherWrite(v) => s.(history := s.history + [Canonical(v)])
    case Deliver => Reconciled(m, s, s.op.value, s.ledger[s.op.value.id]).(op := None)
    case Replay(id) => Reconciled(m, s, s.ops[id], s.ledger[id])
    case ClockStep(_) => s
    case Reload => s
  }

  function Step(m: Model, s: State, e: Event): State requires Enabled(m, s, e) {
    var t := Apply1(m, s, e);
    if e.ClockStep? then t.(now := e.to) else t.(now := s.now + 1)
  }

  // ---- Properties ----

  // S3: an ACK never replaces a cached value that reflects a newer revision.
  predicate NoRegression(pre: State, post: State, ack: nat) {
    pre.cache.None? || post.cache == pre.cache || post.cache.None? || pre.cache.value.rev <= ack
  }

  // S2: an edit base is a pair in the server history, exactly as stored.
  predicate Provenance(s: State) {
    s.editBase.Ok? ==> s.editBase.revision < |s.history| && s.editBase.value == s.history[s.editBase.revision]
  }

  predicate OpInv(m: Model, s: State, o: Op) {
    o.id in s.ops && s.ops[o.id] == o &&
    o.base < |s.history| && Canonical(o.origin) == s.history[o.base] &&
    o.curAtCreate < |s.history| &&
    (o.id in s.ledger ==> s.ledger[o.id] >= o.curAtCreate) &&
    (m == M1 ==> o.created < s.now) &&
    (m == M1 && s.cache.Some? && s.cache.value.observed < o.created ==> s.cache.value.rev <= o.curAtCreate)
  }

  predicate Inv(m: Model, s: State) {
    Wf(s) &&
    (forall r :: 0 <= r < |s.history| ==> Canonical(s.history[r]) == s.history[r]) &&
    (s.cache.Some? ==> s.cache.value.rev < |s.history| && s.cache.value.value == s.history[s.cache.value.rev]) &&
    (s.editBase.Ok? ==> s.editBase.revision < |s.history| && Canonical(s.editBase.value) == s.history[s.editBase.revision]) &&
    (m == M1 ==> Provenance(s)) &&
    (forall id :: id in s.ledger ==> id in s.ops && s.ledger[id] < |s.history| && s.history[s.ledger[id]] == Canonical(s.ops[id].value)) &&
    (forall id :: id in s.ops ==> id < s.nextId) &&
    (s.op.Some? ==> OpInv(m, s, s.op.value))
  }

  lemma CanonicalIdempotent(v: Val)
    ensures Canonical(Canonical(v)) == Canonical(v)
  {}

  lemma InvInit(m: Model)
    ensures Inv(m, Init())
  {}

  lemma AppendKeeps(h: seq<Val>, v: Val)
    requires forall r :: 0 <= r < |h| ==> Canonical(h[r]) == h[r]
    ensures forall r :: 0 <= r < |h + [Canonical(v)]| ==> Canonical((h + [Canonical(v)])[r]) == (h + [Canonical(v)])[r]
  {
    CanonicalIdempotent(v);
    forall r | 0 <= r < |h + [Canonical(v)]|
      ensures Canonical((h + [Canonical(v)])[r]) == (h + [Canonical(v)])[r]
    {
      if r < |h| { assert (h + [Canonical(v)])[r] == h[r]; }
    }
  }

  lemma ReconciledKeepsInv(m: Model, s: State, o: Op, a: nat)
    requires Inv(m, s) && a < |s.history| && s.history[a] == Canonical(o.value)
    ensures var t := Reconciled(m, s, o, a);
      Wf(t) && t.history == s.history && t.ops == s.ops && t.ledger == s.ledger && t.nextId == s.nextId && t.now == s.now &&
      (t.cache.Some? ==> t.cache.value.rev < |t.history| && t.cache.value.value == t.history[t.cache.value.rev]) &&
      (t.editBase.Ok? ==> t.editBase.revision < |t.history| && Canonical(t.editBase.value) == t.history[t.editBase.revision]) &&
      (m == M1 ==> Provenance(t))
  {
    CanonicalIdempotent(o.value);
  }

  lemma InvStep(m: Model, s: State, e: Event)
    requires Inv(m, s) && Enabled(m, s, e)
    ensures Inv(m, Step(m, s, e))
  {
    var t := Step(m, s, e);
    match e
    case Read(r, stored) => {}
    case ReadProjection(r) => {}
    case ReadEditBase(r, stored) => {}
    case Save(v) => {}
    case Resave(v) => {}
    case Send => {
      var o := s.op.value;
      if o.base == Cur(s) && Canonical(o.value) != s.history[Cur(s)] {
        AppendKeeps(s.history, o.value);
        CanonicalIdempotent(o.value);
      }
    }
    case OtherWrite(v) => { AppendKeeps(s.history, v); }
    case Deliver => {
      var o := s.op.value;
      assert s.ops[o.id] == o;
      ReconciledKeepsInv(m, s, o, s.ledger[o.id]);
    }
    case Replay(id) => {
      ReconciledKeepsInv(m, s, s.ops[id], s.ledger[id]);
    }
    case ClockStep(to) => {}
    case Reload => {}
  }

  // A trace: states ss and events es, each event enabled in the state before it.
  predicate Trace(m: Model, ss: seq<State>, es: seq<Event>) {
    |ss| == |es| + 1 && ss[0] == Init() &&
    forall i :: 0 <= i < |es| ==> Enabled(m, ss[i], es[i]) && ss[i + 1] == Step(m, ss[i], es[i])
  }

  predicate NoClock(es: seq<Event>) { forall i :: 0 <= i < |es| ==> !es[i].ClockStep? }
  predicate NoReplay(es: seq<Event>) { forall i :: 0 <= i < |es| ==> !es[i].Replay? }

  lemma Start(m: Model) returns (ss: seq<State>, es: seq<Event>)
    ensures ss == [Init()] && es == [] && Trace(m, ss, es) && NoClock(es) && NoReplay(es)
  {
    ss, es := [Init()], [];
  }

  lemma Push(m: Model, ss: seq<State>, es: seq<Event>, e: Event) returns (ss2: seq<State>, es2: seq<Event>)
    requires Trace(m, ss, es) && Enabled(m, ss[|ss| - 1], e)
    ensures ss2 == ss + [Step(m, ss[|ss| - 1], e)] && es2 == es + [e] && Trace(m, ss2, es2)
    ensures NoClock(es) && !e.ClockStep? ==> NoClock(es2)
    ensures NoReplay(es) && !e.Replay? ==> NoReplay(es2)
  {
    ss2 := ss + [Step(m, ss[|ss| - 1], e)];
    es2 := es + [e];
    forall i | 0 <= i < |es2|
      ensures Enabled(m, ss2[i], es2[i]) && ss2[i + 1] == Step(m, ss2[i], es2[i])
    {
      if i < |es| { assert ss2[i] == ss[i] && ss2[i + 1] == ss[i + 1] && es2[i] == es[i]; }
    }
    if NoClock(es) && !e.ClockStep? {
      forall i | 0 <= i < |es2| ensures !es2[i].ClockStep? { if i < |es| { assert es2[i] == es[i]; } }
    }
    if NoReplay(es) && !e.Replay? {
      forall i | 0 <= i < |es2| ensures !es2[i].Replay? { if i < |es| { assert es2[i] == es[i]; } }
    }
  }

  // Every state a trace reaches satisfies Inv, so a lemma that requires Inv
  // excludes no reachable state.
  lemma TraceInv(m: Model, ss: seq<State>, es: seq<Event>)
    requires Trace(m, ss, es)
    ensures forall i :: 0 <= i < |ss| ==> Inv(m, ss[i])
  {
    InvInit(m);
    var k := 0;
    while k < |es|
      invariant 0 <= k <= |es|
      invariant forall i :: 0 <= i <= k ==> Inv(m, ss[i])
    {
      InvStep(m, ss[k], es[k]);
      k := k + 1;
    }
  }

  // S1 (both models): when the server applies a write, the value it replaces
  // is, canonically, the one the user saw when the intent was formed.
  lemma Intent(m: Model, s: State)
    requires Inv(m, s) && s.op.Some? && s.op.value.base == Cur(s)
    ensures Canonical(s.op.value.origin) == s.history[Cur(s)]
  {}

  // S2 (M1): every edit base is a pair the server stored.
  lemma ProvenanceM1(s: State)
    requires Inv(M1, s)
    ensures Provenance(s)
  {}

  // The ACK revision an event reconciles.
  function AckOf(s: State, e: Event): nat
    requires (e.Deliver? && s.op.Some? && s.op.value.id in s.ledger) || (e.Replay? && e.id in s.ledger)
  {
    if e.Deliver? then s.ledger[s.op.value.id] else s.ledger[e.id]
  }

  // S3 (M1): with O1 and O2, no event that reconciles an ACK regresses the cache.
  lemma NoRegressionM1(s: State, e: Event)
    requires Inv(M1, s) && Enabled(M1, s, e) && (e.Deliver? || e.Replay?)
    ensures NoRegression(s, Step(M1, s, e), AckOf(s, e))
  {
    if e.Deliver? {
      var o := s.op.value;
      var a := s.ledger[o.id];
      if s.cache.Some? && !(s.proj.Some? && a < s.proj.value) {
        var c := s.cache.value;
        NoProofNoApply(Evidence(Valid(a), Absent, At(c.observed), At(o.created), c.value == Canonical(o.value)));
        PlanAckTable(Evidence(Valid(a), Absent, At(c.observed), At(o.created), c.value == Canonical(o.value)));
      }
    }
  }

  // ---- Witnesses ----

  function Last(ss: seq<State>): State requires |ss| >= 1 { ss[|ss| - 1] }

  // Non-vacuity of NoRegressionM1: a reachable M1 Deliver that patches the cache.
  lemma NoRegressionM1Witness() returns (ss: seq<State>, es: seq<Event>)
    ensures Trace(M1, ss, es) && NoClock(es) && NoReplay(es) && |es| >= 1
    ensures var s := ss[|es| - 1];
      es[|es| - 1] == Deliver && Inv(M1, s) && Enabled(M1, s, Deliver) && s.cache.Some? &&
      ss[|es|].cache.Some? && ss[|es|].cache != s.cache
  {
    ss, es := Start(M1);
    ss, es := Push(M1, ss, es, Read(0, true));
    assert Last(ss).cache == Some(Cached(A, 0, 0));
    ss, es := Push(M1, ss, es, ReadEditBase(0, true));
    assert Last(ss).editBase == Ok(A, 0, None, None);
    ss, es := Push(M1, ss, es, Save(B));
    assert Last(ss).op == Some(Op(0, B, 0, 2, 0, A));
    ss, es := Push(M1, ss, es, Send);
    assert Last(ss).history == [A, B] && Last(ss).ledger == map[0 := 1];
    ss, es := Push(M1, ss, es, Deliver);
    assert Last(ss).cache == Some(Cached(B, 0, 1));
    TraceInv(M1, ss, es);
  }

  // M0, S2: a client payload stamped at the ACK revision is not what the server stored.
  lemma StampViolatesProvenanceM0() returns (ss: seq<State>, es: seq<Event>)
    ensures Trace(M0, ss, es) && NoClock(es) && NoReplay(es)
    ensures !Provenance(ss[|es|])
  {
    ss, es := Start(M0);
    ss, es := Push(M0, ss, es, ReadEditBase(0, true));
    assert Last(ss).editBase == Ok(A, 0, None, None);
    ss, es := Push(M0, ss, es, Save(SpacedB));
    assert Last(ss).op == Some(Op(0, SpacedB, 0, 1, 0, A));
    ss, es := Push(M0, ss, es, Send);
    assert Last(ss).history == [A, B] && Last(ss).ledger == map[0 := 1];
    ss, es := Push(M0, ss, es, Deliver);
    assert Last(ss).editBase == Ok(SpacedB, 1, None, Some("ack"));
  }

  // M0, S3 by the clock (#1473): one sender, a backward clock step.
  lemma ClockViolatesNoRegressionM0() returns (ss: seq<State>, es: seq<Event>)
    ensures Trace(M0, ss, es) && NoReplay(es) && |es| >= 1
    ensures var s := ss[|es| - 1];
      es[|es| - 1] == Deliver && s.op.Some? && s.op.value.id in s.ledger &&
      !NoRegression(s, ss[|es|], s.ledger[s.op.value.id])
  {
    ss, es := Start(M0);
    ss, es := Push(M0, ss, es, ReadEditBase(0, true));
    assert Last(ss).editBase == Ok(A, 0, None, None);
    ss, es := Push(M0, ss, es, Save(B));
    assert Last(ss).op == Some(Op(0, B, 0, 1, 0, A));
    ss, es := Push(M0, ss, es, Send);
    assert Last(ss).history == [A, B] && Last(ss).ledger == map[0 := 1];
    ss, es := Push(M0, ss, es, OtherWrite(C));
    assert Last(ss).history == [A, B, C];
    ss, es := Push(M0, ss, es, ClockStep(-60));
    assert Last(ss).now == -60;
    ss, es := Push(M0, ss, es, Read(2, true));
    assert Last(ss).cache == Some(Cached(C, -60, 2));
    ss, es := Push(M0, ss, es, Deliver);
    assert Last(ss).cache == Some(Cached(B, -60, 1));
  }

  // M0, S3 by a second sender (#1072): no clock step. The late replay of the
  // first ACK passes the time proof because the ACK patch kept the read time.
  lemma SecondSenderViolatesNoRegressionM0() returns (ss: seq<State>, es: seq<Event>)
    ensures Trace(M0, ss, es) && NoClock(es) && |es| >= 1
    ensures var s := ss[|es| - 1];
      es[|es| - 1] == Replay(0) && 0 in s.ledger && !NoRegression(s, ss[|es|], s.ledger[0])
  {
    ss, es := Start(M0);
    ss, es := Push(M0, ss, es, Read(0, true));
    assert Last(ss).cache == Some(Cached(A, 0, 0));
    ss, es := Push(M0, ss, es, ReadEditBase(0, true));
    assert Last(ss).editBase == Ok(A, 0, None, None);
    ss, es := Push(M0, ss, es, Save(B));
    assert Last(ss).op == Some(Op(0, B, 0, 2, 0, A));
    ss, es := Push(M0, ss, es, Send);
    assert Last(ss).history == [A, B] && Last(ss).ledger == map[0 := 1];
    ss, es := Push(M0, ss, es, Deliver);
    assert Last(ss).cache == Some(Cached(B, 0, 1)) && Last(ss).editBase == Ok(B, 1, None, Some("ack"));
    ss, es := Push(M0, ss, es, Save(C));
    assert Last(ss).op == Some(Op(1, C, 1, 5, 1, B));
    ss, es := Push(M0, ss, es, Send);
    assert Last(ss).history == [A, B, C] && Last(ss).ledger == map[0 := 1, 1 := 2];
    ss, es := Push(M0, ss, es, Deliver);
    assert Last(ss).cache == Some(Cached(C, 0, 2));
    ss, es := Push(M0, ss, es, Replay(0));
    assert Last(ss).cache == Some(Cached(B, 0, 1));
  }
}
