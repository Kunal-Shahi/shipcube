# State moves from count being added to the legacy shared-builder grants (C-1).
moved {
  from = google_storage_bucket_iam_member.uploads_builder
  to   = google_storage_bucket_iam_member.uploads_builder[0]
}

moved {
  from = google_project_iam_member.builder_ar_writer
  to   = google_project_iam_member.builder_ar_writer[0]
}
