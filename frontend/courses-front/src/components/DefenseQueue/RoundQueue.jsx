import { useEffect, useState } from "react";
import { q, useQueueLanguage, queueError } from "./locale";
import { ErrorNotice } from "./Notice";
import { queueRequest } from "../../api";
import { useRowMotion } from "./useRowMotion";

const resultLabels = { waiting:"Ожидает", called:"Вызван", defended:"Сдал", failed:"Не сдал", no_show:"Не пришёл" };
const notices = {pass:"Результат «Сдал» сохранён",fail:"Результат «Не сдал» сохранён",absent:"Студент перенесён в список отсутствующих",arrived:"Студент добавлен в новый круг",add_student:"Студент добавлен в новый круг"};

export function RoundQueue({ data, action, busy, readOnly = false, ownGithub = "" }) {
  useQueueLanguage();
  const [selected,setSelected]=useState(null);
  const [adding,setAdding]=useState(false);
  const [student,setStudent]=useState("");
  const [notice,setNotice]=useState(null);
  const rows=data.round_entries || [];
  const absent=data.absent_entries || [];
  const first=rows.filter(row=>!row.is_repeat), repeats=rows.filter(row=>row.is_repeat);
  const body=useRowMotion(`${data.session.session_id}:${rows.map(row=>row.row_key).join(',')}`);
  useEffect(()=>{setSelected(null);setNotice(null);setAdding(false);},[data.session.session_id]);
  useEffect(()=>{
    if(!notice)return;
    const timer=setTimeout(()=>setNotice(null),Math.max(0,notice.until-Date.now()));
    return()=>clearTimeout(timer);
  },[notice]);
  async function run(payload){
    setNotice(null);
    const response=await action(payload);
    if(response){
      if(!['call','uncall'].includes(payload.action))setSelected(null);
      if(payload.action==='add_student'){setAdding(false);setStudent("");}
      if(notices[payload.action] && response.undo?.remaining_ms>0)
        setNotice({...response.undo,label:notices[payload.action],until:Date.now()+response.undo.remaining_ms});
    }
    return response;
  }
  async function undo(){
    const old=notice;
    if(!old || busy)return;
    setNotice(null);
    await action({action:"undo",command_id:old.command_id});
    setSelected(null);
  }
  function choose(row){if(!busy)setSelected(value=>value===row.row_key?null:row.row_key);}
  function identity(row){return <><strong>{row.full_name}</strong><br/>{row.github ? <a href={`https://github.com/${encodeURIComponent(row.github)}`} target="_blank" rel="noreferrer" onClick={e=>e.stopPropagation()}>{row.github}</a> : <span className="queue-muted">{q("GitHub не указан")}</span>}<small className="queue-attempt-meta">{row.priority!=null ? `${q("Приоритет")} ${row.priority}` : ""}{row.attempt ? `${row.priority!=null ? " · " : ""}${q(resultLabels[row.attempt.status] || row.attempt.status)}` : ""}</small></>;}
  function rowView(row,index,isAbsent=false){
    const status=row.attempt?.status || 'waiting';
    const picked=selected===row.row_key;
    const available=!readOnly && !busy && data.service_open && (isAbsent ? Date.now()/1000<data.session.ends_at : true);
    const entry=row.attempt?.entry_id;
    return <tr key={row.row_key} data-queue-row={row.row_key} className={`queue-round-row ${picked?'is-selected':''} ${ownGithub && row.github.toLowerCase()===ownGithub.toLowerCase()?'is-own':''} status-${isAbsent?'absent':status}`}
      tabIndex={readOnly?undefined:0} aria-label={`${row.full_name}, ${q(isAbsent?'Не придёт':resultLabels[status])}`} aria-selected={readOnly?undefined:picked}
      onClick={readOnly?undefined:()=>choose(row)} onKeyDown={readOnly?undefined:e=>{if(e.target===e.currentTarget && ['Enter',' '].includes(e.key)){e.preventDefault();choose(row);}}}>
      <td>{index+1}</td><td>{identity(row)}</td><td><div className={`queue-round-actions ${status==='called'?'three-actions':''}`} onClick={e=>e.stopPropagation()}>
      {readOnly ? <span className="queue-status">{q(isAbsent?'Не придёт':resultLabels[status])}</span> : (isAbsent ? <button disabled={!available} className="primary" onClick={()=>run({action:'arrived',student_id:row.student_id})}>{q("Пришёл")}</button>
        : status==='waiting' ? <><button className="primary" disabled={!available || !entry} onClick={()=>run({action:'call',entry_id:entry})}>{q("Вызвать")}</button><button className="queue-absence-action" disabled={!available || !entry} onClick={()=>run({action:'absent',entry_id:entry})}>{q("Не пришёл")}</button></>
        : status==='called' ? <><button className="queue-pass-action" disabled={!available} onClick={()=>run({action:'pass',entry_id:entry})}>{q("Сдал")}</button><button className="queue-fail-action" disabled={!available} onClick={()=>run({action:'fail',entry_id:entry})}>{q("Не сдал")}</button><button disabled={!available} onClick={()=>run({action:'uncall',entry_id:entry})}>{q("Отменить вызов")}</button></> : null)}
      </div></td></tr>;
  }
  const heading=<thead><tr><th>{q("№")}</th><th>{q("Студент / GitHub")}</th><th>{q(readOnly?"Статус":"Действия")}</th></tr></thead>;
  const columns=<colgroup><col className="round-number"/><col className="round-person"/><col className="round-actions"/></colgroup>;
  return <section className={`queue-rounds ${readOnly?"queue-read-only":""}`} aria-label={q("Очередь занятия")}>
    <h3>{q("Придут на занятие")}</h3>
    <div className="queue-table-wrap"><table className="queue-table queue-round-table" aria-label={q("Придут на занятие")}>{columns}{heading}<tbody ref={body}>
      {first.map((row,i)=>rowView(row,i))}
      <tr className="queue-round-divider"><td colSpan={3}><span>{q("Новый круг")}</span></td></tr>
      {repeats.map((row,i)=>rowView(row,first.length+i))}
    </tbody></table>
    {!rows.length && <p className="queue-empty">{q("Пока никто не подтвердил участие.")}</p>}
    {!readOnly && <div className="queue-add-student"><button disabled={busy || !data.service_open || Date.now()/1000>=data.session.ends_at} aria-expanded={adding} onClick={()=>setAdding(!adding)}>{q("Добавить студента")}</button>
      {adding && <form className="queue-toolbar" onSubmit={e=>{e.preventDefault();run({action:'add_student',student_id:Number(student)});}}>
        <label>{q("Студент")}<select required value={student} disabled={busy} onChange={e=>setStudent(e.target.value)}><option value="">{q("Выберите студента")}</option>{(data.student_catalog || []).map(item=><option key={item.student_id} value={item.student_id}>{item.full_name}</option>)}</select></label>
        <button disabled={busy || !student}>{q("Добавить в новый круг")}</button></form>}
    </div>}</div>
    <h3>{q("Не придут на занятие")}</h3><div className="queue-table-wrap"><table className="queue-table queue-round-table" aria-label={q("Не придут на занятие")}>{columns}{heading}<tbody>{absent.map((row,i)=>rowView(row,i,true))}</tbody></table>{!absent.length && <p className="queue-empty">{q("Отсутствующих нет.")}</p>}</div>
    {!readOnly && notice && <div className="queue-undo-notice" role="status" aria-live="polite"><span>{q(notice.label)}</span><button disabled={busy} onClick={undo}>{q("Отменить")}</button><button className="queue-dismiss" aria-label={q("Закрыть уведомление")} onClick={()=>setNotice(null)}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15"/></svg></button><span className="queue-undo-timer" style={{animationDuration:`${notice.remaining_ms}ms`}} /></div>}
  </section>;
}

export function QueuePreferences({course}){
  useQueueLanguage();
  const [config,setConfig]=useState(null),[error,setError]=useState(null),[saved,setSaved]=useState(false),[busy,setBusy]=useState(false);
  useEffect(()=>{let alive=true;setConfig(null);queueRequest(`/admin/courses/${encodeURIComponent(course)}/queue/settings`).then(data=>{if(alive)setConfig(data.preferences);}).catch(err=>{if(alive)setError(err);});return()=>{alive=false;};},[course]);
  function patch(values){setSaved(false);setConfig(old=>({...old,...values}));}
  if(!config)return <p role="status">{error?queueError(error):q("Загружаются настройки…")}</p>;
  return <form className="queue-behavior-settings" onSubmit={async e=>{e.preventDefault();setBusy(true);setError(null);setSaved(false);try{const result=await queueRequest(`/admin/courses/${encodeURIComponent(course)}/queue/preferences`,{method:'PUT',body:{...config,undo_seconds:Number(config.undo_seconds)}});setConfig(result.preferences);setSaved(true);}catch(err){setError(err);}finally{setBusy(false);}}}>
    <h2>{q("Настройки очереди")}</h2>
    <label className="queue-checkbox"><input type="checkbox" checked={config.auto_repeat} disabled={busy} onChange={e=>patch({auto_repeat:e.target.checked})}/>{q("Автоматически добавлять не сдавших в новый круг")}</label>
    <p className="queue-muted">{q("Завершённая попытка остаётся в списке. Повторная создаётся в конце, пока занятие не закончилось.")}</p>
    <label htmlFor="undo-duration">{q("Время для отмены действия, секунд")}</label><div className="queue-duration-controls"><input aria-label={q("Время для отмены действия, секунд")} type="range" min="1" max="10" step="1" disabled={busy} value={config.undo_seconds} onChange={e=>patch({undo_seconds:Number(e.target.value)})}/><input id="undo-duration" type="number" required min="1" max="10" step="1" disabled={busy} value={config.undo_seconds} onChange={e=>patch({undo_seconds:e.target.value})}/></div>
    <button className="primary" disabled={busy}>{q("Сохранить настройки")}</button><p className="queue-settings-feedback" role="status">{saved?q("Настройки сохранены"):busy?q("Сохраняется…"):""}</p><ErrorNotice error={error}/>
  </form>;
}
