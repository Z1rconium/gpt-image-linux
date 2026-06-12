import type { GalleryFilters } from '$lib/stores/gallery';

export type GalleryUrlState = {
  page: number;
  filters: GalleryFilters;
};

function parsePage(value: string | null | undefined) {
  const parsed = Number.parseInt(String(value || ''), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
}

function parseBoolean(value: string | null | undefined) {
  const normalized = String(value || '').toLowerCase();
  return normalized === 'true' || normalized === '1';
}

export function readGalleryUrlState(searchParams: URLSearchParams): GalleryUrlState {
  return {
    page: parsePage(searchParams.get('page')),
    filters: {
      prompt: searchParams.get('prompt') || '',
      model: searchParams.get('model') || '',
      preset: searchParams.get('preset') || '',
      size: searchParams.get('size') || '',
      dateFrom: searchParams.get('date_from') || '',
      dateTo: searchParams.get('date_to') || '',
      favorite: parseBoolean(searchParams.get('favorite'))
    }
  };
}

export function writeGalleryUrlState(searchParams: URLSearchParams, page: number, filters: GalleryFilters) {
  searchParams.delete('page');
  searchParams.delete('prompt');
  searchParams.delete('model');
  searchParams.delete('preset');
  searchParams.delete('size');
  searchParams.delete('date_from');
  searchParams.delete('date_to');
  searchParams.delete('favorite');

  if (page > 1) searchParams.set('page', String(page));
  if (filters.prompt.trim()) searchParams.set('prompt', filters.prompt.trim());
  if (filters.model) searchParams.set('model', filters.model);
  if (filters.preset) searchParams.set('preset', filters.preset);
  if (filters.size) searchParams.set('size', filters.size);
  if (filters.dateFrom) searchParams.set('date_from', filters.dateFrom);
  if (filters.dateTo) searchParams.set('date_to', filters.dateTo);
  if (filters.favorite) searchParams.set('favorite', 'true');
}
