# Run recycle bin

In Studio run history, select one or more finished tasks and click **删除**.
After confirmation they leave run history and appear under **回收站**. Selection
clearing remains available separately as **取消选择**. Running or starting tasks
cannot be recycled; stop them and wait for finalization first. The backend checks
process liveness too, even if a result file already reports a failed outcome.

The recycle bin shows the original task, status, model, and deletion time. Select
items and choose **恢复** to return them to history, or **彻底删除** to remove their
run directory, images, trajectories, and Studio launch record/log. **清空回收站**
applies permanent deletion to the items currently displayed. Both permanent
actions require confirmation and cannot be undone. No automatic expiration is
applied. Benchmark installations, model configuration and global model usage
accounting are unaffected.

Recycling uses an atomic index at `<runs-root>/.studio/trash.json`, with a local
filesystem lock for mutations across Studio and CLI processes. Run files retain
their original paths until permanent deletion, preserving absolute references
in artifacts. Recycling does not reclaim disk space. Restoring removes the index
entry; it does not rerun a task or call a model. Both history and Studio replay
exclude recycled runs. Other external tools directly reading the filesystem can
still read retained files.

A batch is validated before any change. Original directory and file identities
are retained; replaced paths and symlink targets are refused. If permanent file
removal fails, index entries remain in the recycle bin so the error can be fixed
and deletion retried without resurfacing incomplete records. Restoring a record
whose original files are missing is refused. A corrupt index is reported as an
error, rather than interpreted as an empty recycle bin.

## HTTP API

- `POST /api/runs/trash`, body `{"refs": ["RUN_ID_OR_SHORT_ID_OR_LAUNCH_ID"]}`
- `GET /api/trash`: original summaries with `trash_id` and `deleted_at`
- `POST /api/trash/restore`, body `{"ids": ["TRASH_ID"]}`
- `POST /api/trash/delete`, body `{"ids": ["TRASH_ID"]}`

Mutations accept 1 to 500 entries. Permanent deletion only accepts recycle-bin
IDs, never an arbitrary path or a live-history ID.

## CLI

```bash
evoab runs recycle RUN_ID
evoab runs trash --json
evoab runs restore TRASH_ID
evoab runs purge TRASH_ID --yes
```

Use `--runs-root` to select a collection. Existing records need no migration;
without an index the recycle bin is empty. This is a local feature added to the
installed source; it is not an upstream release upgrade.

## Research campaigns and categories

Use `evoab research recycle ID...` to move a finished campaign to the recycle
bin. The **回收站 → 历史研究** category supports restore and permanent deletion.
The API accepts campaign IDs in a `refs` list at `POST /api/research/trash`.
Restore and delete commands accept trash IDs from either category.
`GET /api/trash` includes `category: run|research`; old entries without this
field are interpreted as runs, without migration or loss of restore capability.

The recycle bin provides All, Task Runs and Historical Research categories. Select
all, header selection, restore, and permanent deletion apply to the displayed
category. Clear Current Category never removes the other category; Clear Recycle
Bin on All includes both. Switching categories clears selection. History's
select-all applies to the current filters. Batch deletion rejects the whole selection if any job is active.

Research soft deletion preserves its complete campaign directory, removes the
record from the CLI research list, and blocks stop/resume until restoration. It does
not execute any experiment. Permanent deletion removes that campaign directory.
Its ordinary episode records are managed separately, as stated in the UI.

Because episodes are hashed evidence for campaign recovery, permanently deleting
an episode is refused while its parent campaign still exists, including a recycled
parent. Permanently delete the campaign first, or select the parent and episode
together under All. This prevents restoring a campaign with silently destroyed
trial evidence. Soft-deleting/restoring an episode remains possible independently.
Active campaigns protect their completed episodes too. Background job reaping
shares the recycle lock and cannot recreate a purged launch or campaign record.

Retained research dependencies are now shown on recycled episode rows. The
clear/delete action is disabled when its selection omits a required campaign,
with a visible explanation and the IDs of the retained campaigns. **仅选可删除** selects
only entries whose dependencies are included or no longer exist. A batch rejection
names the blocking research IDs and makes no filesystem changes. Interrupted
setup campaigns with `research.json` but no launch record are included in the
research list, so their dependent evidence is never blocked by an invisible parent.
