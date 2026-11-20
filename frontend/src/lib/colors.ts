// Chart colours. Series palette validated with the dataviz CVD checker:
// cobalt / emerald / amber pass on #FFFFFF (emerald vs terracotta does NOT, so they
// never appear as two series together).
export const C = {
  ink: "#111214",
  muted: "#5E626A",
  border: "#E2E4E9",
  grid: "#EEF0F3",
  cobalt: "#1D4ED8",
  emerald: "#15803D",
  terracotta: "#B91C1C",
  amber: "#B45309",
  surface: "#FFFFFF",
} as const;
