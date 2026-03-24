# Bot Configuration Commands (!set)

Here are the configuration commands available for server owners and administrators:

## 🕵️ Taxman & System
- **`!settaxchannel [#channel]`**: Set the channel where Taxman announcements are sent.
- **`!settaxmantoggle [on/off]`**: Enable or disable the daily 10% tax (Targeting users with > 100,000 JC).
- **`!settaxmanpercent [1-100]`**: Set the specific tax rate percentage (Default: 10%).
- **`!taxstatus`**: View all current Taxman settings, rates, and the next visit countdown.

## 📢 Announcements & Notices
- **`!setnoticechannel [#channel]`**: Set the primary channel for general bot announcements.
- **`!setnotice [message]`**: Broadcast an announcement to the configured notice channel.

## 🎁 Mystery Box Events
- **`!setboxchannel [#channel]`**: Set the channel specifically for Mystery Box event notifications.
- **`!setbox [leg%] [epic%] [rare%] [min]`**: Configure and start a custom loot drop event (Sets legendary, epic, and rare ratios).
- **`!boxrates`**: View the current mystery box drop percentages or event status.

## 🎭 User Customization
- **`!setrole [color]`**: Change the color of your personal custom role (Requires the role-color item from the `!shop`).
