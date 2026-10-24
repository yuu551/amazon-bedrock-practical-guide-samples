import {
  KeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react"
import { Check, ChevronDown } from "lucide-react"

export type SelectOption<T extends string | number> = {
  value: T
  label: string
  description?: string
}

type Props<T extends string | number> = {
  id?: string
  value: T
  onChange: (value: T) => void
  options: readonly SelectOption<T>[]
  ariaLabel?: string
}

export default function Select<T extends string | number>({
  id,
  value,
  onChange,
  options,
  ariaLabel,
}: Props<T>) {
  const rootRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const uid = useId()
  const listId = `${uid}-listbox`
  const triggerId = id ?? `${uid}-trigger`

  const [open, setOpen] = useState(false)
  const selectedIndex = useMemo(
    () => Math.max(0, options.findIndex((opt) => opt.value === value)),
    [options, value],
  )
  const [focusIndex, setFocusIndex] = useState(selectedIndex)
  const activeOptionId = `${uid}-opt-${focusIndex}`

  useEffect(() => {
    if (!open) return
    setFocusIndex(selectedIndex)

    function handleClickOutside(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    function handleEscape(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    document.addEventListener("keydown", handleEscape)
    return () => {
      document.removeEventListener("mousedown", handleClickOutside)
      document.removeEventListener("keydown", handleEscape)
    }
  }, [open, selectedIndex])

  useEffect(() => {
    if (!open || !listRef.current) return
    const active = listRef.current.querySelector<HTMLLIElement>(
      `[data-index="${focusIndex}"]`,
    )
    active?.scrollIntoView({ block: "nearest" })
  }, [open, focusIndex])

  const commit = useCallback(
    (index: number) => {
      const option = options[index]
      if (!option) return
      onChange(option.value)
      setOpen(false)
      triggerRef.current?.focus()
    },
    [onChange, options],
  )

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement | HTMLUListElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault()
      if (!open) {
        setOpen(true)
        return
      }
      const delta = event.key === "ArrowDown" ? 1 : -1
      setFocusIndex((i) => (i + delta + options.length) % options.length)
    } else if (event.key === "Home") {
      if (open) {
        event.preventDefault()
        setFocusIndex(0)
      }
    } else if (event.key === "End") {
      if (open) {
        event.preventDefault()
        setFocusIndex(options.length - 1)
      }
    } else if (event.key === "Enter" || event.key === " ") {
      if (open) {
        event.preventDefault()
        commit(focusIndex)
      } else if (event.key === " ") {
        event.preventDefault()
        setOpen(true)
      }
    } else if (event.key === "Tab") {
      if (open) setOpen(false)
    }
  }

  const selectedOption = options.find((opt) => opt.value === value)

  return (
    <div className={`select-root${open ? " is-open" : ""}`} ref={rootRef}>
      <button
        ref={triggerRef}
        id={triggerId}
        type="button"
        className="select-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={ariaLabel}
        onClick={() => setOpen((prev) => !prev)}
        onKeyDown={handleKeyDown}
      >
        <span className="select-trigger-label">
          {selectedOption?.label ?? "選択してください"}
        </span>
        <ChevronDown className="select-chevron" size={14} aria-hidden="true" />
      </button>

      {open ? (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          className="select-panel"
          tabIndex={-1}
          aria-activedescendant={activeOptionId}
          onKeyDown={handleKeyDown}
        >
          {options.map((option, index) => {
            const selected = option.value === value
            const focused = index === focusIndex
            return (
              <li
                key={String(option.value)}
                id={`${uid}-opt-${index}`}
                role="option"
                aria-selected={selected}
                data-index={index}
                className={[
                  "select-option",
                  selected ? "select-option--selected" : "",
                  focused ? "select-option--focused" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                onMouseEnter={() => setFocusIndex(index)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => commit(index)}
              >
                <span className="select-option-mark" aria-hidden="true">
                  {selected ? <Check size={12} strokeWidth={3} /> : null}
                </span>
                <span className="select-option-body">
                  <span className="select-option-label">{option.label}</span>
                  {option.description ? (
                    <span className="select-option-description">
                      {option.description}
                    </span>
                  ) : null}
                </span>
              </li>
            )
          })}
        </ul>
      ) : null}
    </div>
  )
}
