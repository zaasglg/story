# Demo object images

AI-generated architectural examples for the local demonstration database. These are not documentary photographs of the named construction projects. The API marks them `photo_is_demo=true`, and the app labels them as demo photos.

Populate explicitly after migrations: `python -m backend.seed_object_photos`. It fills active objects without an existing photo, stores PNG bytes in `work_object_photos`, and preserves existing images. Repeat runs do not duplicate or replace photos.
