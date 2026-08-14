import type { QuickJumpRegion } from '../layout/userSettings';

export type RegionFilterOption = {
  value: QuickJumpRegion;
  label: string;
};

export const REGION_FILTER_OPTIONS: RegionFilterOption[] = [
  { value: 'all', label: 'All Regions' },
  { value: 'usa', label: 'USA' },
  { value: 'canada', label: 'Canada' },
  { value: 'international', label: 'International' },
  { value: 'tx', label: 'Texas' },
  { value: 'ca', label: 'California' },
  { value: 'mi', label: 'Michigan' },
  { value: 'ny', label: 'New York' },
];

const REGION_STATE_EQUIVALENTS: Record<QuickJumpRegion, string[]> = {
  all: [],
  usa: [],
  canada: [],
  international: [],
  tx: ['TX', 'TEXAS'],
  ca: ['CA', 'CALIFORNIA'],
  mi: ['MI', 'MICHIGAN'],
  ny: ['NY', 'NEW YORK'],
};

function normalizeLocationValue(value: string | null | undefined): string {
  return (value || '').trim().toUpperCase();
}

function isUsCountry(country: string): boolean {
  return ['USA', 'US', 'UNITED STATES', 'UNITED STATES OF AMERICA'].includes(country);
}

function isCanadaCountry(country: string): boolean {
  return ['CANADA', 'CA'].includes(country);
}

export function normalizeRegionFilter(value: string | null | undefined): QuickJumpRegion {
  const normalized = (value || '').trim().toLowerCase();
  if (normalized === 'usa') return 'usa';
  if (normalized === 'canada') return 'canada';
  if (normalized === 'international') return 'international';
  if (normalized === 'tx') return 'tx';
  if (normalized === 'ca') return 'ca';
  if (normalized === 'mi') return 'mi';
  if (normalized === 'ny') return 'ny';
  return 'all';
}

export function regionLabel(region: QuickJumpRegion): string {
  return REGION_FILTER_OPTIONS.find((option) => option.value === region)?.label || 'All Regions';
}

export function matchesRegionFilter(
  region: QuickJumpRegion,
  stateValue: string | null | undefined,
  countryValue: string | null | undefined,
): boolean {
  if (region === 'all') return true;

  const normalizedCountry = normalizeLocationValue(countryValue);
  const normalizedState = normalizeLocationValue(stateValue);

  if (region === 'usa') return isUsCountry(normalizedCountry);
  if (region === 'canada') return isCanadaCountry(normalizedCountry);
  if (region === 'international') {
    return Boolean(normalizedCountry) && !isUsCountry(normalizedCountry) && !isCanadaCountry(normalizedCountry);
  }

  return REGION_STATE_EQUIVALENTS[region].includes(normalizedState);
}
