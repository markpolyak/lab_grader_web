import { useEffect, useState } from "react";
import { queueError, q } from "./locale";

export function ErrorNotice({ error }) {
  const [dismissed,setDismissed]=useState(null);
  useEffect(()=>{if(!error){setDismissed(null);return;}const timer=setTimeout(()=>setDismissed(error),8000);return()=>clearTimeout(timer);},[error]);
  if(!error || dismissed===error)return null;
  return <div className="queue-error-toast" role="alert"><span>{queueError(error)}</span><button type="button" aria-label={q("Закрыть уведомление")} onClick={()=>setDismissed(error)}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15"/></svg></button></div>;
}
