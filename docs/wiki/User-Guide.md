# User Guide

PRKS organizes research around Works and the entities connected to them. The interface is deliberately closer to a research workspace than to a file manager: a PDF or video can carry bibliographic metadata, research notes, people/roles, tags, progress, annotations, and links into the research network.

## Works

A Work is the central research item. A Work may represent a managed PDF, an online/video source, or another supported research item.

Common Work tasks include:

- editing title and bibliographic metadata;
- tracking reading/viewing progress;
- assigning a Folder and Tags;
- linking People with explicit Roles/credits;
- adding Research Notes and private/reminder-style notes;
- viewing or annotating a managed PDF;
- opening related Concepts, Positions, Arguments/Stances, and graph context.

PRKS keeps source identity separate from descriptive metadata. Video identity in particular is handled as an aggregate rather than as unrelated editable columns; see the [Work source identity design report](https://github.com/Fooftilly/PRKS/blob/master/docs/work-source-identity.md).

## Folders and browse views

Folders provide the main hierarchical organization for the library. Browse surfaces also include Progress, File types, Recently opened, Recently added, search results, and Saved Views.

Supported work-list pages provide bulk selection for operations such as progress changes, folder moves, and tag changes. Bulk mutations are transactional: an invalid target should not leave only part of the selection changed.

## Tags

Tags are reusable vocabulary. They can be attached to Works and Folders. Tag creation/deletion/merge and several tag-assignment paths are part of the local-first system; use the rollout-status document for the exact current coverage.

## People, roles, and groups

People are first-class records rather than free-text author strings. A Work can link to People through Roles/credits. Person Groups organize related people separately from Work Folders.

Remote profile images are optional and deliberately constrained: PRKS accepts direct public image URLs only, refuses private/local targets and redirects, bounds the download, decodes the result, and stores a transcoded cache rather than the original remote bytes.

## Playlists

Playlists organize Works in an explicit order and can be useful for video/research sequences. Playlist changes participate in the durable local-first model where documented by the current rollout status.

## Command palette

Open the command palette with Ctrl+K (Cmd+K on macOS), or use **Search or jump**.

It can navigate to major entities and sections, search the library, create common records, and open Settings/actions. Alt+Enter can route supported destinations into split view.

## Notes and research context

Research Notes live with a Work and use durable local-first operations. PRKS also supports structured research entities—Concepts, Positions, Arguments and Stances—described in [Research Network](Research-Network.md).

## Processing queue

The Processing Files surface is for files that still need to be imported/organized. It is operational UI rather than a normal research document surface, so not every workspace behavior necessarily applies to it.

## Offline expectations

Do not infer offline support from the presence of a page or control. PRKS separates disposable read caches from durable user intent. The exact supported mutation families are tracked in [docs/local-first-rollout-status.md](https://github.com/Fooftilly/PRKS/blob/master/docs/local-first-rollout-status.md).

See [Offline and Sync](Offline-and-Sync.md) for the model.
