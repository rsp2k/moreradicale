# Testing RFC 6638 CalDAV Scheduling with Thunderbird

## Prerequisites
- Mozilla Thunderbird installed
- Radicale test server running on `http://127.0.0.1:5232`

---

## Step 1: Start Radicale Test Server

```bash
cd /home/rpm/claude/radicale/Radicale
python3 -m radicale -C test-scheduling-config.ini
```

**Expected output:**
```
INFO     radicale:__init__.py - Radicale server ready
INFO     radicale:__init__.py - Listening on http://127.0.0.1:5232
```

Keep this terminal open - the server needs to stay running.

---

## Step 2: Open Thunderbird Calendar

1. Launch Thunderbird
2. If Calendar tab isn't visible:
   - Click the **≡** menu (top right)
   - Select **Calendar** or press `Ctrl+Shift+C`

---

## Step 3: Add Alice's Calendar

### Method A: Network Calendar (Recommended)

1. **Right-click** in the calendar list (left sidebar)
2. Select **New Calendar...**
3. Choose **"On the Network"** → **Next**
4. Select **CalDAV** format
5. Enter these details:

   | Field | Value |
   |-------|-------|
   | **Username** | `alice` |
   | **Location** | `http://127.0.0.1:5232/alice/` |

6. Click **Find Calendars**
7. Thunderbird will discover alice's calendar
8. Click **Subscribe** → **Continue**
9. Choose a **Name**: `Alice's Calendar` (or any name)
10. Choose a **Color**: Pick any color you like
11. Click **Finish**

### Method B: Manual Calendar Creation (If Method A fails)

1. Right-click in calendar list → **New Calendar**
2. Choose **"On the Network"** → **Next**
3. Select **CalDAV**
4. Enter:
   - **Username**: `alice`
   - **Location**: `http://127.0.0.1:5232/alice/calendar.ics/`
   - Leave **Password** blank
5. Click **Next** → **Finish**

---

## Step 4: Add Bob's Calendar

Repeat Step 3, but use:
- **Username**: `bob`
- **Location**: `http://127.0.0.1:5232/bob/`
- **Name**: `Bob's Calendar`
- Choose a different color to distinguish

---

## Step 5: Test RFC 6638 Scheduling

### Create Meeting as Alice

1. **Switch to Alice's calendar** (click it in the list)
2. Click **New Event** button or press `Ctrl+I`
3. Fill in event details:

   | Field | Value |
   |-------|-------|
   | **Title** | `Team Standup` |
   | **Start** | Tomorrow at 2:00 PM |
   | **End** | Tomorrow at 3:00 PM |

4. **CRITICAL**: Add attendee
   - Click **Invite Attendees** button (or **Attendees** tab)
   - In the attendee field, type: `bob@localhost`
   - Press **Enter** to add
   - You should see: `bob@localhost (Needs Action)`

5. Click **Save and Close**

### Verify Delivery to Bob

Thunderbird should automatically:
1. Send the invitation via POST to `/alice/schedule-outbox/`
2. Radicale processes and delivers to Bob's inbox
3. Bob's calendar syncs and shows the event

**Check Bob's Calendar:**
- Switch to **Bob's Calendar** in the list
- You should see **"Team Standup"** appear!
- Click the event - you'll see:
  - Organizer: `alice@localhost`
  - Your status: **Needs Action** (not responded yet)

### Bob Accepts the Invitation

1. **In Bob's calendar**, click the "Team Standup" event
2. Look for **Accept** / **Decline** buttons
3. Click **Accept**
4. Thunderbird sends REPLY back to alice@localhost

**Expected Behavior:**
- Bob's status changes to: **Accepted** ✅
- Alice sees Bob's acceptance when she syncs

---

## Step 6: Verify Server Logs

In the terminal running Radicale, you should see:

```
INFO  POST request for '/alice/schedule-outbox/' received
INFO  Routed 1 internal, 0 external attendees
INFO  Delivered iTIP message to /bob/schedule-inbox/...
INFO  POST response status for '/alice/schedule-outbox/': 200 OK
```

---

## Troubleshooting

### Calendar Not Syncing
- **Right-click calendar** → **Synchronize**
- Or wait 1-2 minutes for automatic sync

### No Attendee Field
- Make sure you're creating an **Event**, not a **Task**
- Check that **Invite Attendees** button is visible
- Try enabling it in: Event window → **Options** → **Invite Attendees**

### Event Doesn't Appear in Bob's Calendar
1. Check server logs for errors
2. Verify Bob's calendar is subscribed to `http://127.0.0.1:5232/bob/`
3. Manually sync Bob's calendar (right-click → Synchronize)
4. Check `/tmp/radicale-scheduling-test/collection-root/bob/schedule-inbox/` for files

### Connection Errors
- Verify server is running: `curl http://127.0.0.1:5232`
- Check no firewall blocking port 5232
- Try `http://localhost:5232/` instead of 127.0.0.1

### Authentication Errors
- Our test config has `[auth] type = none`
- Username can be anything, password should be left blank
- If Thunderbird asks for password repeatedly, try canceling

---

## Advanced Testing Scenarios

### Test 1: Multiple Attendees
Create event as Alice, invite both:
- `bob@localhost`
- `charlie@localhost` (you'll need to create charlie's calendar first)

### Test 2: Decline Meeting
As Bob:
1. Open the meeting
2. Click **Decline**
3. Optionally add a comment: "Can't make it, sorry!"
4. Check Alice's calendar to see Bob's declined status

### Test 3: Modify Meeting
As Alice (organizer):
1. Edit the "Team Standup" event
2. Change time to 3:00 PM
3. Save
4. Check Bob's calendar - time should update automatically

### Test 4: Cancel Meeting
As Alice:
1. Open the event
2. Look for **Delete** or **Cancel Event** option
3. If prompted about attendees, choose **Send cancellation**
4. Check Bob's calendar - event should disappear

---

## What You're Testing

### RFC 6638 Features in Action

✅ **Collection Discovery**
- Thunderbird does PROPFIND → sees schedule-inbox-URL, schedule-outbox-URL

✅ **Auto-Creation**
- First access creates `/alice/schedule-inbox/` and `/alice/schedule-outbox/`

✅ **iTIP REQUEST**
- Creating event with attendees → POST to schedule-outbox
- Method: REQUEST
- Organizer: alice@localhost
- Attendee: bob@localhost

✅ **Internal Routing**
- Radicale routes bob@localhost → /bob/ (internal principal)
- Delivers iTIP message to /bob/schedule-inbox/

✅ **Schedule-Response**
- Returns XML with per-attendee status
- `2.0;Success` for Bob (internal delivery succeeded)

✅ **iTIP REPLY** (if you click Accept/Decline)
- Bob's client sends REPLY to alice@localhost
- Updates participation status

---

## Debugging Tips

### Enable Debug Logging
Edit `test-scheduling-config.ini`:
```ini
[logging]
level = debug
```
Restart Radicale to see detailed iTIP processing logs.

### View Raw iCalendar Data
```bash
# See Alice's outbox
ls /tmp/radicale-scheduling-test/collection-root/alice/schedule-outbox/

# See Bob's inbox
ls /tmp/radicale-scheduling-test/collection-root/bob/schedule-inbox/

# View iTIP message
cat /tmp/radicale-scheduling-test/collection-root/bob/schedule-inbox/*.ics
```

### Thunderbird Calendar Storage
Thunderbird caches calendar data locally at:
- **Linux**: `~/.thunderbird/*/calendar-data/`
- **macOS**: `~/Library/Thunderbird/Profiles/*/calendar-data/`
- **Windows**: `%APPDATA%\Thunderbird\Profiles\*\calendar-data\`

### Network Inspector
In Thunderbird:
1. Tools → Developer Tools → **Error Console** (`Ctrl+Shift+J`)
2. Filter for "POST" or "PROPFIND"
3. See raw HTTP requests/responses

---

## Expected Results Summary

| Action | Expected Result |
|--------|----------------|
| Add calendar as alice | PROPFIND discovers schedule-inbox/outbox |
| Create event with bob@localhost | POST to /alice/schedule-outbox/ |
| Server processes | Delivers to /bob/schedule-inbox/ |
| Bob's calendar syncs | Event appears with "Needs Action" status |
| Bob clicks Accept | REPLY sent to alice's inbox |
| Alice syncs | Sees Bob's status as "Accepted" |

---

## Success Criteria

✅ Alice can create events with attendees
✅ Bob automatically receives invitations
✅ Event appears in Bob's calendar without manual import
✅ Bob can Accept/Decline (sends REPLY)
✅ Server logs show iTIP processing
✅ No errors in Thunderbird Error Console

---

## Comparison: With vs Without RFC 6638

### WITHOUT Scheduling (Standard CalDAV)
- Create event → Only appears in Alice's calendar
- Bob doesn't receive anything
- Alice must manually email Bob the .ics file
- Bob imports .ics file manually
- No automatic updates when Alice changes the event

### WITH Scheduling (RFC 6638 - Our Implementation!)
- Create event with attendee → Automatically delivered to Bob
- Bob sees it immediately (after sync)
- Bob can Accept/Decline → Alice sees status update
- Alice modifies event → Bob gets update automatically
- **This is what we just built!** 🎉

---

## Next Steps After Testing

1. ✅ Verify basic scheduling works
2. ⏳ Test edge cases (many attendees, recurring events)
3. ⏳ Test with other clients (Apple Calendar, Evolution)
4. ⏳ Document any bugs or issues found
5. ⏳ Prepare upstream PR if all tests successful

---

**🎉 Happy Testing!**

You're now testing a full RFC 6638 CalDAV Scheduling implementation that's been 11 years in the making for Radicale!
