import { isValidElement } from "react";
import Tooltip from "@mui/material/Tooltip";
import { q } from "./locale";

// Text stays discoverable with a keyboard; controls keep their native action.
export function Hint({ text, children, control = false }) {
  return <Tooltip title={q(text)} describeChild arrow enterDelay={350} leaveDelay={150}
    enterTouchDelay={500} leaveTouchDelay={4000}
    slotProps={{ tooltip: { className: "queue-tooltip" }, arrow: { className: "queue-tooltip-arrow" } }}>
    <span className={control ? "queue-hint-control" : "queue-hint"} tabIndex={control ? (isValidElement(children) && children.props.disabled ? 0 : undefined) : 0}>{children}</span>
  </Tooltip>;
}
