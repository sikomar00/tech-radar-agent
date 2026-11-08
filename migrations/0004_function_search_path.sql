-- 함수가 호출한 쪽의 search_path를 따라가지 않게 고정한다(Supabase 보안 어드바이저 경고). 본문이 다른 객체를 쓰지 않으므로 빈 값으로 둔다
alter function forbid_change() set search_path = '';
