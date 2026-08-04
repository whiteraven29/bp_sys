# College Inventory Management System

## User Manual

**Intended user:** Estate Officer  
**Account creator:** System Administrator

---

## 1. Purpose of the Inventory System

The Inventory Management System records and tracks college property. It helps the Estate Officer know:

- Which assets belong to the college.
- Where every asset is currently located.
- Which person or office is responsible for an asset.
- The current condition of each asset.
- Which assets have been transferred, repaired, inspected, or disposed of.
- The quantity of assets in each college location.

The administrator creates the Estate Officer account but cannot access inventory records or inventory reports.

---

## 2. Signing In

1. Open the college system login page.
2. Enter the username provided by the administrator.
3. Enter the password.
4. Select **Sign In**.

After signing in, the Estate Officer is taken to the Inventory workspace.

Use **Sign out** when finishing work, especially on a shared computer.

---

## 3. Inventory Navigation

The Inventory sidebar contains:

- **Overview** — summary cards and inventory graphs.
- **Assets**
  - **Asset Register** — search, review, and edit registered assets.
  - **Register Asset** — register the stock found in one office or import it from Excel.
- **Inventory Setup** — define offices, rooms, floors, corridors, common areas, categories, and reusable item types before stock registration.
- **Transfers** — move assets between locations.
- **Maintenance** — record faults, repairs, costs, and completion details.
- **Physical Inspections** — conduct and record physical stock checks.
- **Disposal** — record proposed and completed asset disposals.
- **Reports** — download inventory records as Excel files.

The **Assets** menu can be expanded or collapsed. The complete Inventory sidebar can also be collapsed using the menu button in the top header. On a phone or tablet, the same button opens and closes the sidebar drawer.

---

## 4. College Asset Tag Convention

Every physical asset should have its own unique asset number/tag and quantity `1`.

Recommended examples:

| Asset | Example tag |
|---|---|
| Table | `BPCH/TB/1` |
| CPU | `BPCH/CPU/1` |
| Chair | `BPCH/CH/1` |
| Monitor | `BPCH/MON/1` |

Continue the number for additional items:

- `BPCH/CH/1`
- `BPCH/CH/2`
- `BPCH/CH/3`
- up to `BPCH/CH/30`, for example.

### Important tagging rules

- Never assign the same tag to two assets.
- Attach or mark the recorded tag on the corresponding physical asset.
- Use quantity `1` for individually tagged college property.
- Register a CPU and monitor separately because each is a separate physical asset.
- Use consistent uppercase prefixes such as `BPCH/CPU`, `BPCH/MON`, `BPCH/CH`, and `BPCH/TB`.
- Check the Asset Register before starting a new number sequence.

For 30 chairs, use 30 individual tags rather than one tag with quantity 30. Automatic numbering or Excel import makes this faster.

---

## 5. Inventory Overview

Select **Overview** to see:

- Number of asset records.
- Total item quantity.
- Number of locations holding assets.
- Assets in poor or unserviceable condition.
- Assets grouped by condition.
- Asset quantities grouped by location.

Use the Overview to identify locations or conditions requiring attention. Detailed changes must be made from the appropriate menu, such as Asset Register, Transfers, or Maintenance.

### Office Asset Map

The **Office Asset Map** on the Overview shows the relationship between the college, its inventory locations, and registered assets.

1. Select an office/location node to expand it.
2. Review every asset connected to that office. Node colors show good, fair, or attention-needed condition.
3. Select an asset node to see its tag, category, quantity, condition, location, and responsible office.
4. Select **Edit asset** from the details panel when a record needs updating.
5. Select **All offices** or the central office node to return to the complete location map.

---

## 6. Initial Inventory Setup

Before recording physical stock, open **Inventory Setup** and define:

- Every relevant location in the managed building. A location can be an office, room, floor, corridor, stairway, store, reception, outdoor area, or other common space.
- The asset categories used by the college.
- Reusable item types such as Office Chair, Desk, CPU, Monitor, and Cabinet.

Each reusable item type records its name, category, optional description, and default tag prefix. This setup is completed once and reused across every office.

The system includes an initial catalogue containing Office Chair, Student Chair, Office Table, Student Table, Fixed Desk, Shelf, Cabinet, Mouse, Keyboard, CPU, Monitor, Printer, Projector, Projecting Board, Notice Board, Whiteboard, Dustbin, Mop, Fire Extinguisher, Safe Custody Box, and Extension Cable. Add other small items from **Reusable Item Types** when required.

### Register stock by office

1. Open **Assets → Register Asset**.
2. Under **Register Stock by Location**, choose the office, room, floor, corridor, or common area.
3. Enter the responsible person or office once.
4. Select **Add Item** for every type of asset found in that office.
5. For each row, choose the item type and confirm its tag prefix, starting number, quantity, and condition.
6. Select **Register Location Stock**.

For a chair row with prefix `BPCH/CH`, starting number `1`, and quantity `30`, the system creates 30 individually tracked assets from `BPCH/CH/1` through `BPCH/CH/30`. The complete office submission is atomic: if any generated tag conflicts, nothing from that submission is saved.

The **Single Asset / Edit Existing Asset** form remains available for exceptional one-off assets and corrections to existing records.

---

## 7. Registering Many Assets with Excel

### Excel registration

Excel follows the same office-first quantity-expansion workflow.

### Download the template

1. Open **Assets → Register Asset**.
2. Under **Bulk Register from Excel**, select **Download Excel Template**.
3. Save the template without changing its worksheet names or headings.

### Complete the Assets worksheet

Enter one office/item-type combination per row. A quantity of 30 is expanded into 30 separately tagged records.

Columns marked with `*` are required:

- Office/Location
- Responsible Person/Office
- Item Type
- Tag Prefix
- Starting Number
- Quantity
- Condition

Description is optional.

Use the dropdown choices included in the template for category, location, and condition.

### Validate and import

1. Select the completed `.xlsx` file.
2. Select **Validate File**.
3. Review any reported errors.
4. Correct the specified Excel rows and validate again.
5. When validation succeeds, select **Confirm Import**.

Validation does not save records. Records are saved only after confirmation. If any row is invalid, the system imports nothing, preventing an incomplete batch.

### Common Excel errors

- Duplicate asset tag in the file.
- Asset tag already exists in the system.
- Required value is missing.
- Category or location does not match the template list.
- Condition is not an accepted value.
- Quantity is not a positive whole number.
- Worksheet name or column headings were changed.

---

## 8. Asset Register and Editing

Open **Assets → Asset Register** to view all registered items.

The register shows:

- Asset tag.
- Asset name and description.
- Category.
- Current location.
- Responsible person or office.
- Quantity.
- Condition.

Use the search box to find an asset by tag, name, description, location, category, or responsible office.

### Edit an asset

1. Find the asset in the register.
2. Select **Edit**.
3. Update the necessary details in the registration form.
4. Select **Save Item**.

Do not manually change an asset's location to represent a movement. Use **Transfers** so the system preserves the movement history.

---

## 9. Locations and Categories

Select **Locations** to add a new location or asset category.

The system initially includes:

- ICT & Examination Office
- Finance & Administration Office
- Staff Office
- Director's Office
- Secretary's Office
- Principal's Office
- Library
- Level 4 Classroom
- Level 5 Classroom
- Level 6 Classroom
- Cleaners' Office
- Security Guard Area
- Other

If an exact location is not available, add a clear new location instead of repeatedly using **Other**. For example, use `ICT Store Room` when that is the actual location.

Avoid creating duplicate locations with slightly different spelling.

---

## 10. Transferring an Asset

Use Transfers whenever an asset physically moves to another office or area.

1. Select **Transfers**.
2. Select the asset or batch.
3. Select the new location.
4. Enter the new responsible person or office.
5. Enter the quantity being transferred.
6. Enter the transfer date and reason.
7. Select **Record Transfer**.

For normally tagged assets, transfer quantity `1`.

The system updates the current location and responsible office while preserving the previous location in transfer history.

### Existing grouped records

Some older records may contain a quantity greater than 1 under one tag. If only part of such a group is transferred, the system:

1. Reduces the quantity remaining at the original location.
2. Creates a traceable split tag for the transferred quantity.
3. Assigns the split record to the new location.

New registrations should use individual tags and quantity `1` wherever the physical items can be tagged.

---

## 11. Maintenance

Use Maintenance to record faults, servicing, or repairs.

1. Select **Maintenance**.
2. Select the affected asset.
3. Enter the quantity affected—normally `1`.
4. Enter the reported date.
5. Describe the issue.
6. Select the current status:
   - Reported
   - In progress
   - Completed
7. Add the provider, repair cost, and action taken when known.
8. When completed, enter the completion date.
9. Select **Save Maintenance**.

A completion date is required when the status is **Completed**.

Use **Edit** on an existing maintenance record to update progress, action taken, cost, provider, or completion information.

For an older grouped record, selecting only part of the quantity creates a separately traceable asset tag for the affected items.

---

## 12. Physical Inspections

Physical inspections compare system records with assets physically found at a location.

### Create an inspection

1. Select **Physical Inspections**.
2. Select the location.
3. Enter the inspection date and inspector's name.
4. Add optional notes.
5. Keep the status **Open** while verification is underway.
6. Select **Create Inspection**.

### Record item results

Open the inspection and record each asset as:

- **Found** — asset is present and correctly located.
- **Missing** — asset cannot be found.
- **Damaged** — asset is present but damaged.
- **Relocated** — asset is found somewhere different from its recorded location.

After checking all items and resolving discrepancies, change the inspection status to **Closed**.

If an asset is relocated, record a Transfer so its official current location is updated.

---

## 13. Disposal

Do not delete an asset when it is damaged, obsolete, sold, donated, or otherwise removed from service. Use Disposal to preserve its history.

### Propose disposal

1. Select **Disposal**.
2. Select the asset.
3. Enter the proposed date.
4. Enter the reason.
5. Set status to **Proposed**.
6. Select **Save Disposal**.

### Complete disposal

1. Select **Edit** on the proposed disposal record.
2. Change the status to **Disposed**.
3. Enter the disposal method.
4. Enter the disposal date.
5. Add the approval or disposal reference where available.
6. Save the record.

Disposal method and disposal date are required when an item is marked **Disposed**.

---

## 14. Reports

Select **Reports** to download Excel reports for:

- Asset Register
- Transfers
- Maintenance
- Physical Inspections
- Disposals

Downloaded reports can be used for management review, stocktaking, audit preparation, and printing.

Before producing an official report, confirm that transfers, maintenance updates, inspections, and disposals have been entered.

---

## 15. Recommended Working Routine

### When assets arrive

1. Confirm the next available tag number.
2. Physically mark each asset.
3. Register each tagged asset with quantity 1.
4. Confirm its location and responsible office.

### When an asset moves

1. Locate it by tag.
2. Record a Transfer.
3. Confirm the new location in the Asset Register.

### When an asset develops a fault

1. Locate it by tag.
2. Create a Maintenance record.
3. Update the record until completed.

### During stocktaking

1. Create one inspection for each location.
2. Check every listed asset physically.
3. Record missing, damaged, or relocated assets.
4. Record transfers or maintenance actions needed.
5. Close the inspection.

### When an asset leaves service

1. Create a Disposal proposal.
2. Complete the disposal record after authorization and physical disposal.
3. Retain the record for audit purposes.

---

## 16. Data Quality and Security

- Never share the Estate Officer password.
- Sign out after use.
- Do not reuse asset tags.
- Do not invent a location when the correct location already exists.
- Record transfers immediately after movement.
- Update maintenance records when work is completed.
- Never delete history to correct a movement or disposal.
- Verify Excel files before confirming import.
- Keep physical labels readable and attached to their assets.
- Download reports periodically for management review.

---

## 17. Troubleshooting

### “Asset number/tag already registered”

Search the Asset Register for the tag. Use the correct next number or update the existing record if it represents the same physical asset.

### “Choose a category/location from the template list”

Download the latest template and use its dropdown values. If a new location or category is required, add it in the system first and download a new template.

### Excel import will not confirm

Validation must succeed before confirmation. Correct every reported row and validate the file again.

### Transfer quantity is rejected

The entered quantity is greater than the quantity currently recorded for that asset. Check the Asset Register and the physical items.

### Completed maintenance is rejected

Enter the completion date before saving a maintenance record as Completed.

### Disposal is rejected

When status is Disposed, enter both the disposal method and disposal date.

### An asset appears in the wrong location

Use Transfers to move it to the correct location. Do not simply overwrite the location because that would omit the movement explanation.

### The sidebar is hidden

Use the menu button in the top header to reopen it. On mobile, the same button opens the navigation drawer.
