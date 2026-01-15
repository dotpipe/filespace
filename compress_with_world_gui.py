import threading
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from compress_with_world import compress, decompress


def browse_open(entry):
    path = filedialog.askopenfilename()
    if path:
        entry.delete(0, tk.END)
        entry.insert(0, path)
        try:
            size = os.path.getsize(path)
            entry.master.master.input_size_var.set(f'{size} bytes')
        except Exception:
            entry.master.master.input_size_var.set('')


def browse_save(entry):
    path = filedialog.asksaveasfilename(defaultextension='')
    if path:
        entry.delete(0, tk.END)
        entry.insert(0, path)
        try:
            # output size unknown yet
            entry.master.master.output_size_var.set('')
        except Exception:
            pass


def run_op(op, infile_entry, outfile_entry, world_entry, btn):
    infile = infile_entry.get()
    outfile = outfile_entry.get()
    world = world_entry.get() or 'world_package.bin'
    if not infile or not outfile:
        messagebox.showerror('Missing', 'Please select input and output files')
        return
    status_lbl = infile_entry.master.master.status_lbl
    progress = infile_entry.master.master.progress
    input_var = infile_entry.master.master.input_size_var
    output_var = infile_entry.master.master.output_size_var

    def target():
        try:
            btn.config(state='disabled')
            status_lbl.config(text=f'{op.title()} running...')
            progress.start(50)
            if op == 'compress':
                compress(infile, outfile, world)
            else:
                decompress(infile, outfile, world)
            # update sizes
            try:
                input_var.set(f"{os.path.getsize(infile)} bytes")
            except Exception:
                input_var.set('')
            try:
                output_var.set(f"{os.path.getsize(outfile)} bytes")
            except Exception:
                output_var.set('')
            status_lbl.config(text=f'{op.title()} finished')
            messagebox.showinfo('Done', f'{op.title()} finished')
        except Exception as e:
            messagebox.showerror('Error', str(e))
        finally:
            progress.stop()
            btn.config(state='normal')

    threading.Thread(target=target, daemon=True).start()


def make_row(root, label_text, browse_mode='open'):
    frm = tk.Frame(root)
    lbl = tk.Label(frm, text=label_text, width=12, anchor='w')
    ent = tk.Entry(frm, width=60)
    btn = tk.Button(frm, text='Browse', command=lambda: browse_open(ent) if browse_mode == 'open' else browse_save(ent))
    lbl.pack(side='left')
    ent.pack(side='left', padx=4)
    btn.pack(side='left', padx=4)
    return frm, ent


def main():
    root = tk.Tk()
    root.title('compress_with_world — GUI')

    f1, infile = make_row(root, 'Input:', 'open')
    f1.pack(pady=4, padx=8)

    # size label for input
    root.input_size_var = tk.StringVar(value='')
    tk.Label(root, textvariable=root.input_size_var).pack()

    f2, outfile = make_row(root, 'Output:', 'save')
    f2.pack(pady=4, padx=8)

    # size label for output
    root.output_size_var = tk.StringVar(value='')
    tk.Label(root, textvariable=root.output_size_var).pack()

    f3, world = make_row(root, 'World:', 'open')
    f3.pack(pady=4, padx=8)

    btn_frame = tk.Frame(root)
    compress_btn = tk.Button(btn_frame, text='Compress', width=12,
                             command=lambda: run_op('compress', infile, outfile, world, compress_btn))
    decompress_btn = tk.Button(btn_frame, text='Decompress', width=12,
                               command=lambda: run_op('decompress', infile, outfile, world, decompress_btn))
    compress_btn.pack(side='left', padx=8)
    decompress_btn.pack(side='left', padx=8)
    btn_frame.pack(pady=10)

    # progress and status
    root.progress = ttk.Progressbar(root, mode='indeterminate', length=300)
    root.progress.pack(pady=4)
    root.status_lbl = tk.Label(root, text='')
    root.status_lbl.pack()

    note = tk.Label(root, text='World defaults to world_package.bin if empty')
    note.pack(pady=(0, 8))

    root.mainloop()


if __name__ == '__main__':
    main()
