"use strict";
// The lane scheduler: the line-for-line twin of gantry/schedule.py (#9).
// tests/test_schedule_agreement.py holds the two to the same starts, ends and
// lanes. Keys are looked up in a Map or Set only.
(function () {
  function check(items) {
    const keys = new Set();
    for (const item of items) {
      if (keys.has(item.key)) throw new Error("plan: duplicate key " + JSON.stringify(item.key));
      keys.add(item.key);
    }
    for (const item of items) {
      for (const pred of item.preds) {
        if (!keys.has(pred)) {
          throw new Error("plan: " + JSON.stringify(item.key) + " waits on " + JSON.stringify(pred) + ", which is not an item");
        }
      }
      const hours = item.hours;
      if (typeof hours !== "number" || !Number.isFinite(hours) || hours <= 0) {
        throw new Error("plan: " + JSON.stringify(item.key) + " has hours " + String(hours) + "; hours must be a finite number above 0");
      }
    }
  }

  // A Map of key to [start, end, lane], hours from 0.
  function plan(items, lanes) {
    if (typeof lanes !== "number" || !Number.isInteger(lanes) || lanes < 1) {
      throw new Error("plan: lanes must be a whole number from 1");
    }
    check(items);
    const free = new Array(lanes).fill(0);
    const out = new Map();
    const pending = items.slice();
    while (pending.length) {
      let li = 0;
      for (let i = 1; i < lanes; i++) {
        if (free[i] < free[li]) li = i;
      }
      const now = free[li];
      const ready = pending.filter((item) => item.preds.every((p) => out.has(p) && out.get(p)[1] <= now));
      if (!ready.length) {
        const later = [];
        for (const planned of out.values()) {
          if (planned[1] > now) later.push(planned[1]);
        }
        if (!later.length) throw new Error("plan: no item can start; the waits hold a cycle");
        free[li] = Math.min(...later);
        continue;
      }
      let pick = ready[0];
      for (const item of ready.slice(1)) {
        if (item.eff_priority < pick.eff_priority
            || (item.eff_priority === pick.eff_priority && item.wbs_index < pick.wbs_index)) {
          pick = item;
        }
      }
      const end = now + pick.hours;
      out.set(pick.key, [now, end, li]);
      free[li] = end;
      pending.splice(pending.indexOf(pick), 1);
    }
    return out;
  }

  // The hours of the heaviest chain of waits, over every item.
  function chainHours(items) {
    check(items);
    const succ = new Map(items.map((item) => [item.key, []]));
    const indeg = new Map(items.map((item) => [item.key, item.preds.length]));
    const byKey = new Map(items.map((item) => [item.key, item]));
    for (const item of items) {
      for (const pred of item.preds) succ.get(pred).push(item.key);
    }
    const queue = items.filter((item) => indeg.get(item.key) === 0).map((item) => item.key);
    const best = new Map();
    let head = 0;
    while (head < queue.length) {
      const key = queue[head];
      head += 1;
      let before = 0;
      for (const pred of byKey.get(key).preds) before = Math.max(before, best.get(pred));
      best.set(key, before + byKey.get(key).hours);
      for (const s of succ.get(key)) {
        indeg.set(s, indeg.get(s) - 1);
        if (indeg.get(s) === 0) queue.push(s);
      }
    }
    if (queue.length < items.length) throw new Error("plan: no item can start; the waits hold a cycle");
    return best.size ? Math.max(...best.values()) : 0;
  }

  // "chain" when the plan ends with the heaviest chain, else "capacity".
  function limit(items, lanes) {
    const p = plan(items, lanes);
    let end = 0;
    for (const planned of p.values()) end = Math.max(end, planned[1]);
    return end <= chainHours(items) ? "chain" : "capacity";
  }

  globalThis.gantrySchedule = Object.freeze({ plan, limit, chainHours });
})();
