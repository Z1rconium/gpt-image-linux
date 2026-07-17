import type { GalleryExportJobStatusValue, GalleryImportJobStatusValue, GallerySyncJobStatusValue } from './common';

export type GalleryEntry = {
  id: string;
  prompt: string;
  size: string;
  filename: string;
  image_url?: string | null;
  thumbnail_filename?: string | null;
  thumbnail_url?: string | null;
  thumbnail_status?: 'ready' | 'queued' | 'missing';
  created_at: string;
  completed_at?: string | null;
  image_width?: number | null;
  image_height?: number | null;
  model?: string | null;
  quality?: string | null;
  output_format?: string | null;
  output_compression?: number | null;
  response_format?: string | null;
  n?: number | null;
  api_path?: string | null;
  api_preset_name?: string | null;
  duration?: string | null;
  favorite: boolean;
  bytes?: number | null;
};

export type GalleryResponse = {
  total: number;
  total_bytes: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_prev: boolean;
  has_next: boolean;
  next_cursor?: string | null;
  prev_cursor?: string | null;
  images: GalleryEntry[];
  filter_options: {
    models: string[];
    presets: string[];
    sizes: string[];
  };
};

export type MessageResponse = {
  status: string;
  message: string;
};

export type GalleryBatchResponse = {
  status: string;
  count: number;
  file_count?: number;
  requested_count?: number;
  updated_count?: number;
  missing_count?: number;
  missing_ids?: string[];
};

export type GallerySelectionTokenResponse = {
  selection_token: string;
  count: number;
  expires_at: string;
};

export type GalleryExportJobStatus = {
  job_id: string;
  status: GalleryExportJobStatusValue;
  stage?: string | null;
  message?: string | null;
  progress: number;
  filename?: string | null;
  download_url?: string | null;
  requested_count: number;
  processed_count: number;
  exported_count: number;
  missing_count: number;
  bytes_total: number;
  bytes_written: number;
  created_at?: string | null;
  updated_at?: string | null;
  error?: string | null;
};

export type GallerySyncJobStatus = {
  job_id: string;
  status: GallerySyncJobStatusValue;
  stage?: string | null;
  message?: string | null;
  progress: number;
  created_at?: string | null;
  updated_at?: string | null;
  error?: string | null;
  total_count: number;
  compared_count: number;
  uploaded_count: number;
  pending_upload_count: number;
  skipped_existing_count: number;
  missing_local_count: number;
  failed_count: number;
  bytes_total: number;
  bytes_uploaded: number;
  dry_run: boolean;
  checkpoint_filename?: string | null;
};

export type GalleryImportJobStatus = {
  job_id: string;
  status: GalleryImportJobStatusValue;
  stage?: string | null;
  message?: string | null;
  progress: number;
  requested_count: number;
  processed_count: number;
  imported_count: number;
  skipped_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  error?: string | null;
};

