import { Database } from 'lucide-react'

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

interface DatasetPickerProps {
  dataset: string
  available: string[]
  live: string[]
  onChange: (dataset: string) => void
}

/** The page header's dataset Select (web/DESIGN.md §4), labelled "Dataset". */
export function DatasetPicker({ dataset, available, live, onChange }: DatasetPickerProps) {
  return (
    <Select value={dataset} onValueChange={onChange}>
      <SelectTrigger aria-label="Dataset" className="h-8 min-w-36 gap-2 text-xs">
        <Database className="size-3.5 text-muted-foreground" />
        <SelectValue placeholder="Dataset" />
      </SelectTrigger>
      <SelectContent align="end">
        {available.map((schema) => (
          <SelectItem key={schema} value={schema} className="text-xs">
            <span>{schema}</span>
            {live.includes(schema) && (
              <span className="ml-2 rounded-full bg-primary/15 px-1.5 py-px text-[10px] font-medium text-primary">
                Live
              </span>
            )}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
