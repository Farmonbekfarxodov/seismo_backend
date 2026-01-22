from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json


@csrf_exempt
def set_reference_segment(request):
    """
    AJAX so‘rov orqali tanlangan anomaliya segmentini reference sifatida saqlaydi.
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            segment = data.get('segment')

            if not segment or not all(k in segment for k in ['index', 'start', 'end']):
                return JsonResponse({
                    'status': 'error',
                    'message': 'Segment ma\'lumotlari to\'liq emas (index, start, end kerak)'
                }, status=400)

            # Sessionda saqlash (keyinchalik DB modelga o‘tkazish mumkin)
            request.session['selected_reference_segment'] = {
                'index': segment['index'],
                'start': segment['start'],
                'end': segment['end'],
                # Agar values yuborilgan bo‘lsa: 'values': segment.get('values', [])
            }
            request.session.modified = True

            return JsonResponse({
                'status': 'success',
                'message': 'Reference segment saqlandi',
                'selected': segment
            })

        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Noto‘g‘ri JSON formati'}, status=400)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    return JsonResponse({'status': 'error', 'message': 'Faqat POST so‘rov'}, status=405)