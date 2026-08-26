import { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  Button,
  CardEmpty,
  CardRow,
  Chip,
  FieldRadioGroup,
  FieldSelect,
  FieldStepper,
  FieldText,
  FieldToggle,
  Modal,
  Stat,
  Table,
  renderCell,
  type TableColumn,
} from './index';

describe('Button', () => {
  it('names an icon-only button from its aria-label', () => {
    render(<Button iconOnly icon={<svg />} aria-label="Match timer" />);
    expect(screen.getByRole('button', { name: 'Match timer' })).toBeInTheDocument();
  });

  it('disables and marks busy while loading', () => {
    render(<Button loading>Save</Button>);
    const button = screen.getByRole('button', { name: 'Save' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
  });

  it('does not fire onClick while loading', () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Save
      </Button>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(onClick).not.toHaveBeenCalled();
  });

  it('defaults to type="button" so it never submits a form by accident', () => {
    render(<Button>Filter</Button>);
    expect(screen.getByRole('button', { name: 'Filter' })).toHaveAttribute('type', 'button');
  });

  it('keeps an explicit submit type', () => {
    render(<Button type="submit">Save entry</Button>);
    expect(screen.getByRole('button', { name: 'Save entry' })).toHaveAttribute('type', 'submit');
  });

  // An anchor cannot be `disabled`. A disabled-looking link that still
  // navigates is worse than no link at all.
  it('strips href and tab order from a disabled link', () => {
    render(
      <Button as="a" href="#/settings" aria-disabled>
        Settings
      </Button>,
    );
    const link = screen.getByText('Settings').closest('a');
    expect(link).not.toHaveAttribute('href');
    expect(link).toHaveAttribute('tabindex', '-1');
  });

  it('exposes pressed state to assistive tech', () => {
    render(<Button pressed>Filter</Button>);
    expect(screen.getByRole('button', { name: 'Filter' })).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('Chip', () => {
  it('builds a remove label from its text', () => {
    render(
      <Chip onRemove={() => undefined} tone="accent">
        Swerve
      </Chip>,
    );
    expect(screen.getByRole('button', { name: 'Remove Swerve' })).toBeInTheDocument();
  });

  it('takes an explicit remove label when the child is not a string', () => {
    render(
      <Chip onRemove={() => undefined} removeLabel="Remove team 1114">
        <strong>1114</strong>
      </Chip>,
    );
    expect(screen.getByRole('button', { name: 'Remove team 1114' })).toBeInTheDocument();
  });
});

describe('Stat', () => {
  it('announces confidence rather than leaving it to the bar alone', () => {
    render(<Stat label="Offense" value="4/5" confidence={0.82} />);
    expect(screen.getByLabelText('Confidence 82 percent')).toBeInTheDocument();
  });

  // A confidence over 1 would draw a bar wider than its track, which reads as
  // more certainty than the data supports.
  it('clamps confidence into 0–1', () => {
    const { rerender } = render(<Stat label="Offense" value="4" confidence={1.4} />);
    expect(screen.getByLabelText('Confidence 100 percent')).toBeInTheDocument();
    rerender(<Stat label="Offense" value="4" confidence={-0.2} />);
    expect(screen.getByLabelText('Confidence 0 percent')).toBeInTheDocument();
  });

  it('omits confidence entirely when none is supplied', () => {
    render(<Stat label="Fouls" value="3" />);
    expect(screen.queryByLabelText(/Confidence/)).not.toBeInTheDocument();
  });

  it('signs a positive trend', () => {
    render(<Stat label="Climb" value="92" trend={4} trendSuffix="%" />);
    expect(screen.getByText('+4%')).toBeInTheDocument();
  });
});

describe('Card insides', () => {
  it('makes an interactive row a real button', () => {
    const onClick = vi.fn();
    render(<CardRow label="Match log" value="12" onClick={onClick} />);
    fireEvent.click(screen.getByRole('button', { name: /Match log/ }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('leaves a static row out of the tab order', () => {
    render(<CardRow label="Drivetrain" value="Swerve" />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('renders an empty state with its own copy', () => {
    render(<CardEmpty title="No pit data yet">Ask at the pit.</CardEmpty>);
    expect(screen.getByText('No pit data yet')).toBeInTheDocument();
    expect(screen.getByText('Ask at the pit.')).toBeInTheDocument();
  });
});

describe('Field', () => {
  it('wires the label to the control', () => {
    render(<FieldText label="Scout name" />);
    const input = screen.getByLabelText('Scout name');
    expect(input.tagName).toBe('INPUT');
  });

  it('links hint and error through aria-describedby', () => {
    render(<FieldText label="Team" hint="Digits only" error="Required" />);
    const input = screen.getByLabelText('Team');
    const describedBy = input.getAttribute('aria-describedby') ?? '';
    const ids = describedBy.split(' ').filter(Boolean);
    expect(ids).toHaveLength(2);
    const described = ids.map((id) => document.getElementById(id)?.textContent);
    expect(described).toContain('Digits only');
    expect(described).toContain('Required');
  });

  it('marks an errored control invalid and announces the message', () => {
    render(<FieldText label="Team" error="Enter a team number." />);
    expect(screen.getByLabelText('Team')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a team number.');
  });

  it('leaves a clean control without aria-invalid', () => {
    render(<FieldText label="Team" />);
    expect(screen.getByLabelText('Team')).not.toHaveAttribute('aria-invalid');
  });

  it('gives each field its own id even when labels repeat', () => {
    render(
      <>
        <FieldText label="Notes" />
        <FieldText label="Notes" />
      </>,
    );
    const [first, second] = screen.getAllByLabelText('Notes');
    expect(first.id).not.toBe(second.id);
    expect(first.id).toBeTruthy();
  });

  it('wires a select the same way', () => {
    render(
      <FieldSelect label="Event">
        <option>2026arc</option>
      </FieldSelect>,
    );
    expect(screen.getByLabelText('Event').tagName).toBe('SELECT');
  });

  it('names the radio group from its legend', () => {
    render(
      <FieldRadioGroup
        legend="Match phase"
        name="phase"
        value="auto"
        onValueChange={() => undefined}
        options={[
          { value: 'auto', label: 'Auto' },
          { value: 'teleop', label: 'Teleop' },
        ]}
      />,
    );
    expect(screen.getByRole('group', { name: 'Match phase' })).toBeInTheDocument();
    expect(screen.getByLabelText('Auto')).toBeChecked();
  });

  it('renders a provenance badge beside the label', () => {
    render(<FieldStepper label="High goal" value={2} onValueChange={() => undefined} badge={<span>ML</span>} />);
    expect(screen.getByText('ML')).toBeInTheDocument();
  });

  // The 1-5 scales used to be six plain buttons: a screen reader heard six
  // unrelated controls with no signal that one was selected, and arrow keys
  // did nothing. Segmented keeps the visual and restores the semantics.
  it('keeps radiogroup semantics in the segmented variant', () => {
    render(
      <FieldRadioGroup
        legend="Offense level"
        name="offense"
        segmented
        value="3"
        onValueChange={() => undefined}
        options={[
          { value: '1', label: '1' },
          { value: '2', label: '2' },
          { value: '3', label: '3' },
          { value: '', label: 'N/A' },
        ]}
      />,
    );
    expect(screen.getByRole('group', { name: /Offense level/ })).toBeInTheDocument();
    expect(screen.getAllByRole('radio')).toHaveLength(4);
    expect(screen.getByRole('radio', { name: '3' })).toBeChecked();
  });

  it('reports the chosen segment, including the empty N/A option', () => {
    const onValueChange = vi.fn();
    render(
      <FieldRadioGroup
        legend="Offense level"
        name="offense"
        segmented
        value="3"
        onValueChange={onValueChange}
        options={[
          { value: '3', label: '3' },
          { value: '', label: 'N/A' },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole('radio', { name: 'N/A' }));
    expect(onValueChange).toHaveBeenCalledWith('');
  });

  it('exposes the toggle as a switch with its state', () => {
    render(<FieldToggle label="Auto-advance" checked onCheckedChange={() => undefined} />);
    expect(screen.getByRole('switch', { name: 'Auto-advance' })).toBeChecked();
  });
});

describe('FieldStepper', () => {
  function Harness({ start = 0, min = 0, max = 5 }: { start?: number; min?: number; max?: number }) {
    const [value, setValue] = useState(start);
    return (
      <FieldStepper label="High goal" name="high goal" value={value} onValueChange={setValue} min={min} max={max} />
    );
  }

  it('labels both buttons with what they change', () => {
    render(<Harness />);
    expect(screen.getByRole('button', { name: 'Increase high goal' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Decrease high goal' })).toBeInTheDocument();
  });

  it('counts up and down', () => {
    render(<Harness start={2} />);
    fireEvent.click(screen.getByRole('button', { name: 'Increase high goal' }));
    expect(screen.getByLabelText('High goal')).toHaveValue(3);
    fireEvent.click(screen.getByRole('button', { name: 'Decrease high goal' }));
    expect(screen.getByLabelText('High goal')).toHaveValue(2);
  });

  it('stops at both bounds instead of going out of range', () => {
    render(<Harness start={0} min={0} max={2} />);
    expect(screen.getByRole('button', { name: 'Decrease high goal' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Increase high goal' }));
    fireEvent.click(screen.getByRole('button', { name: 'Increase high goal' }));
    expect(screen.getByLabelText('High goal')).toHaveValue(2);
    expect(screen.getByRole('button', { name: 'Increase high goal' })).toBeDisabled();
  });

  // Clearing the field mid-edit parses to NaN. Writing that into a scouting
  // record loses the count the scout already entered.
  it('keeps the last good value when the field is cleared', () => {
    render(<Harness start={4} max={9} />);
    fireEvent.change(screen.getByLabelText('High goal'), { target: { value: '' } });
    expect(screen.getByLabelText('High goal')).toHaveValue(4);
  });

  // 1 + 0.1 + 0.1 is 1.2000000000000002 in binary floating point, and that
  // renders in full inside the field.
  it('does not accumulate floating-point noise on a fractional step', () => {
    function Fractional() {
      const [value, setValue] = useState(1);
      return (
        <FieldStepper label="Rank weight" value={value} onValueChange={setValue} min={0} max={4} step={0.1} />
      );
    }
    render(<Fractional />);
    const increase = screen.getByRole('button', { name: 'Increase Rank weight' });
    fireEvent.click(increase);
    fireEvent.click(increase);
    fireEvent.click(increase);
    expect(screen.getByLabelText('Rank weight')).toHaveValue(1.3);
  });

  it('clamps a typed value that exceeds the maximum', () => {
    render(<Harness start={1} max={5} />);
    fireEvent.change(screen.getByLabelText('High goal'), { target: { value: '99' } });
    expect(screen.getByLabelText('High goal')).toHaveValue(5);
  });
});

describe('Modal', () => {
  afterEach(() => {
    document.body.style.overflow = '';
  });

  it('renders as a labelled modal dialog', () => {
    render(
      <Modal open onClose={() => undefined} title="Share this event">
        <p>Body</p>
      </Modal>,
    );
    const dialog = screen.getByRole('dialog', { name: 'Share this event' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
  });

  it('renders nothing when closed', () => {
    render(
      <Modal open={false} onClose={() => undefined} title="Share">
        <p>Body</p>
      </Modal>,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('closes on Escape and on a backdrop press', () => {
    const onClose = vi.fn();
    const { container } = render(
      <Modal open onClose={onClose} title="Share">
        <p>Body</p>
      </Modal>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.mouseDown(screen.getByRole('dialog').parentElement!);
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(container).toBeTruthy();
  });

  it('ignores Escape and the backdrop when not dismissible', () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Delete entries?" dismissible={false}>
        <p>Body</p>
      </Modal>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.mouseDown(screen.getByRole('dialog').parentElement!);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /^Close/ })).not.toBeInTheDocument();
  });

  it('locks page scroll while open and releases it on close', () => {
    const { rerender } = render(
      <Modal open onClose={() => undefined} title="Share">
        <p>Body</p>
      </Modal>,
    );
    expect(document.body.style.overflow).toBe('hidden');
    rerender(
      <Modal open={false} onClose={() => undefined} title="Share">
        <p>Body</p>
      </Modal>,
    );
    expect(document.body.style.overflow).toBe('');
  });

  // Two stacked modals both lock. Closing the inner one must not hand the
  // scrollbar back to a page that still has a modal over it.
  it('refcounts the scroll lock across stacked modals', () => {
    const { rerender } = render(
      <>
        <Modal open onClose={() => undefined} title="Outer">
          <p>Outer</p>
        </Modal>
        <Modal open onClose={() => undefined} title="Inner">
          <p>Inner</p>
        </Modal>
      </>,
    );
    expect(document.body.style.overflow).toBe('hidden');
    rerender(
      <>
        <Modal open onClose={() => undefined} title="Outer">
          <p>Outer</p>
        </Modal>
        <Modal open={false} onClose={() => undefined} title="Inner">
          <p>Inner</p>
        </Modal>
      </>,
    );
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('restores focus to whatever opened it', () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Open
          </button>
          <Modal open={open} onClose={() => setOpen(false)} title="Share">
            <p>Body</p>
          </Modal>
        </>
      );
    }
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Open' });
    opener.focus();
    fireEvent.click(opener);
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Close Share' }));
    expect(document.activeElement).toBe(opener);
  });

  it('wraps Tab from the last focusable back to the first', () => {
    render(
      <Modal
        open
        onClose={() => undefined}
        title="Share"
        footer={<button type="button">Copy link</button>}
      >
        <button type="button">Inside</button>
      </Modal>,
    );
    const last = screen.getByRole('button', { name: 'Copy link' });
    last.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close Share' }));
  });
});

describe('Table', () => {
  type Row = { team: string; rank: number; rp: number };
  const columns: TableColumn<Row>[] = [
    { key: 'team', label: 'Team', sortable: true },
    { key: 'rank', label: 'Rank', numeric: true, sortable: true },
    { key: 'rp', label: 'RP', numeric: true },
  ];
  const rows: Row[] = [
    { team: 'frc1114', rank: 1, rp: 34 },
    { team: 'frc254', rank: 2, rp: 32 },
  ];

  it('renders a real table with row headers', () => {
    render(<Table columns={columns} rows={rows} rowKey={(row) => row.team} />);
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getAllByRole('rowheader')).toHaveLength(2);
  });

  it('shows the empty state instead of an empty table', () => {
    render(<Table columns={columns} rows={[]} rowKey={(row) => row.team} empty="No rankings yet." />);
    expect(screen.getByText('No rankings yet.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('starts a new column ascending and flips the active one', () => {
    const onSort = vi.fn();
    render(
      <Table
        columns={columns}
        rows={rows}
        rowKey={(row) => row.team}
        sortBy={{ key: 'rank', direction: 'asc' }}
        onSort={onSort}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Rank/ }));
    expect(onSort).toHaveBeenLastCalledWith('rank', 'desc');

    fireEvent.click(screen.getByRole('button', { name: /Team/ }));
    expect(onSort).toHaveBeenLastCalledWith('team', 'asc');
  });

  it('exposes the sorted column through aria-sort', () => {
    render(
      <Table
        columns={columns}
        rows={rows}
        rowKey={(row) => row.team}
        sortBy={{ key: 'rank', direction: 'desc' }}
        onSort={() => undefined}
      />,
    );
    const header = screen.getByRole('columnheader', { name: /Rank/ });
    expect(header).toHaveAttribute('aria-sort', 'descending');
  });

  it('leaves a non-sortable column without a sort control', () => {
    render(<Table columns={columns} rows={rows} rowKey={(row) => row.team} onSort={() => undefined} />);
    expect(screen.queryByRole('button', { name: /^RP/ })).not.toBeInTheDocument();
  });

  it('renders custom cells', () => {
    render(
      <Table
        columns={[{ key: 'team', label: 'Team', render: (row: Row) => <b>{row.team.toUpperCase()}</b> }]}
        rows={rows}
        rowKey={(row) => row.team}
      />,
    );
    expect(screen.getByText('FRC1114')).toBeInTheDocument();
  });

  it('shows an em dash rather than blank for a missing value', () => {
    render(
      <Table
        columns={[{ key: 'team', label: 'Team' }, { key: 'missing', label: 'Missing' }]}
        rows={rows}
        rowKey={(row) => row.team}
      />,
    );
    expect(screen.getAllByText('—')).toHaveLength(2);
  });

  // A six-column table on a 390px phone is unreadable however it scrolls.
  it('becomes stacked cards below the breakpoint', () => {
    const listeners: Array<() => void> = [];
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        addEventListener: (_: string, handler: () => void) => listeners.push(handler),
        removeEventListener: () => undefined,
      })),
    );

    render(<Table columns={columns} rows={rows} rowKey={(row) => row.team} />);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getByText('frc1114')).toBeInTheDocument();
    // Every non-first column still appears, as a labelled row inside the card.
    expect(screen.getAllByText('Rank')).toHaveLength(2);

    vi.unstubAllGlobals();
  });

  // Stacking is the right default, but a compact standings grid beats one card
  // per row when the values are short.
  it('lets a page supply its own narrow layout', () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      })),
    );

    render(
      <Table
        columns={columns}
        rows={rows}
        rowKey={(row) => row.team}
        renderCards={(narrow) => <ul>{narrow.map((r) => <li key={r.team}>{r.team} standings</li>)}</ul>}
      />,
    );
    expect(screen.getByText('frc1114 standings')).toBeInTheDocument();
    // The default stacked cards must not also render.
    expect(screen.queryByText('Rank')).not.toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it('keeps sorting reachable on the card layout', () => {
    const onSort = vi.fn();
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      })),
    );

    render(
      <Table
        columns={columns}
        rows={rows}
        rowKey={(row) => row.team}
        sortBy={{ key: 'rank', direction: 'asc' }}
        onSort={onSort}
      />,
    );
    const chips = screen.getAllByRole('button');
    fireEvent.click(chips[0]);
    expect(onSort).toHaveBeenCalled();

    vi.unstubAllGlobals();
  });

  it('scopes its horizontal scroll to its own container', () => {
    const { container } = render(<Table columns={columns} rows={rows} rowKey={(row) => row.team} />);
    const scroller = container.firstElementChild as HTMLElement;
    expect(within(scroller).getByRole('table')).toBeInTheDocument();
    expect(scroller.className).toBeTruthy();
  });

  it('marks the row a page wants emphasised', () => {
    render(
      <Table
        columns={columns}
        rows={rows}
        rowKey={(row) => row.team}
        rowClassName={(row) => row.team === 'frc254' && 'is-you'}
      />,
    );
    const marked = screen.getAllByRole('row').filter((row) => row.classList.contains('is-you'));
    expect(marked).toHaveLength(1);
    expect(within(marked[0]).getByText('frc254')).toBeInTheDocument();
  });

  it('renderCell formats a cell exactly as the table does', () => {
    // The point of exporting it: a page building a narrow view gets the same
    // output as the column, so the two can never drift.
    const withRender: TableColumn<Row> = {
      key: 'rp',
      label: 'RP',
      render: (row) => `${row.rp} RP`,
    };
    expect(renderCell(withRender, rows[0])).toBe('34 RP');
    expect(renderCell(columns[2], rows[0])).toBe(34);
    expect(renderCell({ key: 'missing', label: 'Missing' }, rows[0])).toBe('—');
  });
});
