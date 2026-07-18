export interface CompanyBalancedItem {
  posting: {
    company: string;
    company_slug: string;
  };
}

export interface CompanyOverflow {
  company: string;
  companySlug: string;
  hiddenCount: number;
  totalCount: number;
}

export function balanceTodayCompanies<T extends CompanyBalancedItem>(
  items: readonly T[],
  expandedCompanySlugs: ReadonlySet<string>,
  perCompanyLimit = 2,
): { visibleItems: T[]; overflows: CompanyOverflow[] } {
  if (!Number.isInteger(perCompanyLimit) || perCompanyLimit < 1) {
    throw new RangeError("perCompanyLimit must be a positive integer");
  }

  const seenByCompany = new Map<string, number>();
  const companyNames = new Map<string, string>();
  const visibleItems: T[] = [];

  for (const item of items) {
    const companySlug = item.posting.company_slug;
    const seen = (seenByCompany.get(companySlug) ?? 0) + 1;
    seenByCompany.set(companySlug, seen);
    if (!companyNames.has(companySlug)) {
      companyNames.set(companySlug, item.posting.company);
    }
    if (seen <= perCompanyLimit || expandedCompanySlugs.has(companySlug)) {
      visibleItems.push(item);
    }
  }

  const overflows = Array.from(seenByCompany, ([companySlug, totalCount]) => ({
    company: companyNames.get(companySlug) ?? companySlug,
    companySlug,
    hiddenCount: Math.max(0, totalCount - perCompanyLimit),
    totalCount,
  })).filter((group) => group.hiddenCount > 0);

  return { visibleItems, overflows };
}
