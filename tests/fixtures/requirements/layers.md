# Alpha requirements

A fictional site's requirements, one or more in every layer.

## Business

- BR-1: Grow the harbor trade.
- BRule-1: A berth holds one vessel at a time.

## Use cases

- UC-1: Book a berth. {needs: [FR-1, 'QA-1']}
- UC-2: Cancel a booking. {needs: FR-1}
- UC-3: Browse the schedule.

## System

- SR-1: The system keeps a berth register.
- FR-1: The register lists every berth
  with its length
  and its depth
  - sub point
- FR-2: Bookings are checked against length. {refines: SR-1}
  This line continues the text.
- FR-3: Each booking names a vessel
  and a date. {refines: "SR-1"}
- QA-1: A booking is confirmed within one second.
- EI-1: The register is read from a CSV export.
- CON-1: The system runs on the harbor office computer.
