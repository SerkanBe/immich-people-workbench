# Connect with an Immich API key

On the Workbench connection page, choose **API key** and enter the Immich URL
and a key for the account whose library you want to review. An Immich
administrator can instead use **Admin login** if password login is enabled.

## Create a key

In Immich, open your profile menu, choose **Account Settings → API Keys**, and
create a key with these ten permissions:

| Permission | Used for |
| --- | --- |
| `user.read` | Verify the account |
| `person.read` | Read people and face previews |
| `person.statistics` | Read photo counts |
| `person.update` | Name, hide, restore, or set a representative face |
| `person.create` | Create a person when separating a face |
| `person.merge` | Merge people |
| `asset.read` | Search photos and read metadata |
| `asset.view` | Display photo previews |
| `face.read` | Read assigned faces |
| `face.update` | Reassign an incorrect face |

Paste the key into Workbench. If a permission is missing, the connection page
names it; update the key in Immich and reconnect. A key with `all` or extra
permissions may connect with a warning, but Workbench cannot limit the key's
authority. It does not need delete, upload, original-download, or key-management
permissions.

## Credentials and saved progress

The key stays in Workbench server memory and is never saved to disk. The
browser remembers your Immich URL; the login form clears its credential fields
after connecting or switching methods. Re-enter your key after logout, eight
hours, or a Workbench restart. Saved progress remains.

Local progress belongs to the normalized Immich URL and account ID used when the
state directory first connects. A replacement key for the same account and
server keeps Pending and review progress. Use a separate `--state-dir` for a
different account or server; Workbench does not import or combine directories.

## Review and Sync

Naming, drafting, and reviewing do not change Immich. Workbench checks the
account and required permissions again before applying changes. If a key is
revoked or loses a required permission, Sync is refused and Pending stays saved.
Update the key, reconnect to the same account and server, review Pending, then
choose **Sync** yourself.

Immich still controls which people and assets an account may access. A key does
not bypass ownership, sharing, or locked-asset rules. Merges may not have a
universal undo; inspect each change in Pending before Sync. If Workbench reports
an uncertain merge result, check both people in Immich before retrying or
removing the request. Removing Pending does not undo a merge that already
happened.
