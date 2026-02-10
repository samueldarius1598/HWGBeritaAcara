-- Berita Acara module schema
-- Enable pgcrypto if not enabled (needed for gen_random_uuid)
-- create extension if not exists "pgcrypto";

create table if not exists public.berita_acara_header (
  id uuid primary key default gen_random_uuid(),
  no_form text not null,
  purpose_id text not null,
  purpose_name text not null,
  outlet_id text not null,
  outlet_name text not null,
  dibuat_oleh jsonb default '[]'::jsonb,
  disetujui_oleh jsonb default '[]'::jsonb,
  mengetahui_oleh jsonb default '[]'::jsonb,
  status text not null default 'SUBMITTED',
  created_at timestamptz not null default now()
);

create index if not exists idx_berita_acara_header_no_form
  on public.berita_acara_header (no_form);

create table if not exists public.berita_acara_lines (
  id uuid primary key default gen_random_uuid(),
  header_id uuid not null references public.berita_acara_header(id) on delete cascade,
  row_no int not null,
  nama_item text not null,
  kode_item text,
  uom text,
  qty numeric not null,
  product_id text,
  remarks text
);

create index if not exists idx_berita_acara_lines_header_id
  on public.berita_acara_lines (header_id);
