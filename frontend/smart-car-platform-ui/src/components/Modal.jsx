// Centered dialog with a backdrop. Closes on Escape or backdrop click.
//
// Usage:
//   <Modal
//     open={open}
//     title="Add device"
//     onClose={() => setOpen(false)}
//     footer={<><button className="btn" onClick={close}>Cancel</button>
//               <button className="btn btn-primary" onClick={save}>Save</button></>}
//   >
//     ...form fields...
//   </Modal>
import { useEffect } from 'react';

export default function Modal({ open, title, onClose, footer, children }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === 'Escape' && onClose?.();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="btn btn-icon" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}
