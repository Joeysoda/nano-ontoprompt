# Plan B blind validation packet

Use the fixed frePPLe ontology `frepple-d7eb98078882b234c395fd05` with source
commit `73e5be3d1573db043209111325dd921d68cf4b88` and source SHA-256
`d7eb98078882b234c395fd053c5f6fbda33810cb90add2adb4bf7d62f28637ef`.

Give this page to each participant without the sibling known-answer JSON. Run
the same questions with one new user, one manufacturing/domain user, and one
Agent. Do not demonstrate the path first. Record the chosen object type,
filters/links/actions used, final answer, elapsed time, help requests, and any
wrong turns. A refresh or browser back/forward operation is part of the task
where requested.

| ID | Blind task | Required evidence |
| --- | --- | --- |
| B01 | Find the business object type that represents customer demand. Report total objects and the open/closed split. | Type selected, result/aggregate values. |
| B02 | Find every open demand with quantity at least 20 using nested filters. Report names and quantities. | Saved filter expression and result rows. |
| B03 | Start at `Demand 01` and use linked navigation to identify its item, customer, and location. | Three link choices and linked object names. |
| B04 | Find the open demand with the largest quantity and report its due date. | Sort/filter path and selected object panel. |
| B05 | Group open demands by item. Report demand count and total quantity for each item. | Pivot/aggregate configuration and values. |
| B06 | Compare the two customer groups for open demand count and quantity. | Compared sets or grouped aggregate values. |
| B07 | Build the set “all open demands minus open demands with quantity at least 20.” Report its members. | Set expression and result rows. |
| B08 | Save the open-demand query as an Exploration, refresh the browser, reopen it, and explain what was preserved. | Resource ID, restored definition, elapsed time. |
| B09 | Pin the current data, save a complete List, and explain how this differs from B08. | Data view ID, List member count, explanation. |
| B10 | Find all suppliers and determine which items each supplies, including lead time and minimum order size. | Supplier objects, pivot path, five supply rows. |
| B11 | Find the operation with a six-hour fixed duration and distinguish it from operations that use per-unit duration. | Operation name and duration fields. |
| B12 | Open `Demand 01` in the shared Object Panel, open the relation graph, return with browser history, and confirm the same object and query remain selected. | Object ID, graph state, restored URL/state. |

For each participant, append a row to the table below. `Correct` is 0–12.
`Unassisted` counts tasks completed without help. Classify errors as discovery,
wrong type, wrong link direction, static/dynamic confusion, permission,
scenario/live confusion, or execution failure.

| Participant | Role | Correct | Unassisted | Median seconds | Help requests | Error types | Notes |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| pending | new user |  |  |  |  |  |  |
| pending | domain user |  |  |  |  |  |  |
| pending | Agent |  |  |  |  |  |  |
