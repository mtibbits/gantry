# B

- [ ] Blockers
  - [ ] Two days {issue: Issue-1, est: 12h, h: 0}
  - [x] Done {issue: Issue-2, est: 6h, h: 0}
  - [P] Parked {issue: Issue-3, est: 6h, h: 0}
  - [ ] Too long {issue: Issue-4, est: 90h, h: 0}
  - [ ] Absent on b {issue: Issue-12, est: 6h, h: 0, depends_on: [zz:Issue-1]}
  - [ ] Behind the absent one {issue: Issue-13, est: 6h, h: 0, depends_on: [Issue-12]}
- [ ] A row {issue: Issue-5}
  - [ ] One day {issue: Issue-6, est: 6h, h: 0}
  - [ ] Three days {issue: Issue-7, est: 18h, h: 0}
  - [x] Done under the row {issue: Issue-11, est: 6h, h: 0}
- [ ] Priority
  - [ ] The P3 blocker {issue: Issue-8, priority: P3, est: 6h, h: 0}
  - [ ] The P2 leaf {issue: Issue-9, priority: P2, est: 6h, h: 0}
  - [ ] After the long one {issue: Issue-10, est: 6h, h: 0, depends_on: [Issue-4]}
